"""Reader emotion, optional punctuation, and publication continuation contracts."""
import unittest
from unittest.mock import Mock

from test_context_comment_drafts import ARTICLE, DRAFT, REVIEW, response
from comment_drafts import ContextCommentDrafts, valid_comment_text, validate_draft
from comment_tasks import comment_posted, comment_draft_ready


class ReaderReactionTests(unittest.TestCase):
    def test_user_examples_are_valid_without_forcing_a_period(self):
        for text in ('原来压花还能让纸巾不易分层，学到了学到了！',
                     '原来植物睡眠运动也算感性运动的一种',
                     '原来香蕉自己释放乙烯就能催熟，长知识了！'):
            with self.subTest(text=text):
                self.assertTrue(valid_comment_text(text))

    def test_simple_discovery_does_not_require_a_scoring_adjective(self):
        text='原来被理解的感觉还和人的认知有关'
        resolver=Mock(model='offline-model')
        resolver._post.side_effect=[response(dict(DRAFT,comment=text)),response(REVIEW)]
        result=ContextCommentDrafts(resolver).generate(ARTICLE)
        self.assertEqual(result['comment'],text)
        self.assertFalse(result['publish_authorized'])

    def test_emotional_reaction_is_not_trimmed_or_rewritten(self):
        text='人与技术的关系还得这样看，学到了学到了！'
        resolver=Mock(model='offline-model')
        resolver._post.side_effect=[response(dict(DRAFT,comment=text)),response(REVIEW)]
        result=ContextCommentDrafts(resolver).generate(ARTICLE)
        self.assertEqual(result['comment'],text)
        self.assertEqual(resolver._post.call_count,2)

    def test_unsupported_emotional_claim_still_requires_review(self):
        resolver=Mock(model='offline-model')
        resolver._post.side_effect=[response(dict(DRAFT,comment='原来所有机器都懂人的感情，长知识了！')),
                                   response(dict(REVIEW,grounded=False))]
        engine=ContextCommentDrafts(resolver)
        self.assertIsNone(engine.generate(ARTICLE))
        self.assertEqual(engine.last_error,'review_rejected')

    def test_repeated_or_unattached_punctuation_is_not_accepted(self):
        for text in ('原来真实交流还有这样的作用！！','原来真实交流还有这样的作用!！',
                     '原来真实交流还有这样的作用。！',
                     '，原来真实交流还有这样的作用','原来真实交流，，还有这样的作用',
                     '原来真实交流还有这样的作用\n'):
            with self.subTest(text=text):
                self.assertFalse(valid_comment_text(text))

    def test_natural_short_clauses_or_a_question_can_reach_fact_review(self):
        for text in ('原来真实交流！还有这样的作用','原来真实交流，还有这样的作用，学到了',
                     '原来真实交流还有这样的作用？'):
            self.assertTrue(valid_comment_text(text))

    def test_existing_period_and_single_ascii_exclamation_are_accepted(self):
        for text in ('真实交流的作用还真不少。','真实交流的作用还真不少!','真实交流的作用还真不少'):
            self.assertTrue(valid_comment_text(text))

    def test_question_width_can_confirm_input_and_the_same_posted_comment(self):
        text='叶片夜里还会合起来，还有哪些植物会这样？'
        draft=[dict(text=text.replace('？','?'),box=[25,1155,540,32]),
               dict(text='发布',box=[630,1210,50,32])]
        self.assertEqual(comment_draft_ready(draft,text),draft[-1])
        posted=[dict(text=text.replace('？','?'),box=[85,1000,540,32]),
                dict(text='刚刚·回复',box=[85,1060,140,32]),dict(text='删除',box=[650,1060,55,32])]
        self.assertTrue(comment_posted(posted,text))
        posted[0]['text']=posted[0]['text'].replace('夜里','白天')
        self.assertFalse(comment_posted(posted,text))

    def test_emotion_does_not_allow_fabricated_evidence_or_out_of_scope_promises(self):
        self.assertIsNone(validate_draft(dict(DRAFT,comment='人的认知还会这样起作用，长知识了！',
            evidence='正文完全不存在的引用依据'),ARTICLE))
        self.assertFalse(valid_comment_text('人工智能绝对不会有局限，长知识了！'))

    def test_own_posted_exclamation_is_a_normal_continuation_page(self):
        text='人与技术的关系还得这样看，学到了学到了！'
        records=[dict(text=text,box=[85,1000,540,32]),
                 dict(text='刚刚·回复',box=[85,1060,140,32]),
                 dict(text='删除',box=[650,1060,55,32])]
        self.assertTrue(comment_posted(records,text))
        self.assertFalse(comment_posted(records+[dict(text='访问验证',box=[200,100,180,40])],text))

    def test_input_ocr_exclamation_width_is_equivalent_but_changed_words_are_not(self):
        text='人与技术的关系还得这样看，学到了学到了！'
        records=[dict(text=text.replace('！','!'),box=[25,1155,540,32]),
                 dict(text='发布',box=[630,1210,50,32])]
        self.assertEqual(comment_draft_ready(records,text),records[1])
        records[0]['text']=text.replace('关系','效果')
        self.assertIsNone(comment_draft_ready(records,text))
        records[0]['text']=text[:-1]
        self.assertIsNone(comment_draft_ready(records,text))

    def test_posted_ocr_exclamation_width_can_resume_the_same_comment(self):
        text='人与技术的关系还得这样看，学到了学到了！'
        records=[dict(text=text.replace('！','!'),box=[85,1000,540,32]),
                 dict(text='刚刚·回复',box=[85,1060,140,32]),
                 dict(text='删除',box=[650,1060,55,32])]
        self.assertTrue(comment_posted(records,text))
        records[0]['text']=text.replace('关系','效果')
        self.assertFalse(comment_posted(records,text))

    def test_longer_natural_reaction_can_wrap_in_the_editor(self):
        text='化石上的啃食痕迹还能这样反推植物运动，长知识了！'
        self.assertTrue(valid_comment_text(text))
        rows=[dict(text=text[:18],box=[25,1110,500,30]),
              dict(text=text[18:],box=[25,1155,200,30]),
              dict(text='发布',box=[630,1210,50,32])]
        self.assertEqual(comment_draft_ready(rows,text),rows[-1])
        rows[1]['box'][0]=400
        self.assertIsNone(comment_draft_ready(rows,text))
        rows[1]['box'][0]=25;rows[1]['box'][1]=1185
        self.assertIsNone(comment_draft_ready(rows,text))

    def test_wrapped_own_comment_can_resume_without_combining_distant_rows(self):
        text='化石上的啃食痕迹还能这样反推植物运动，长知识了！'
        rows=[dict(text=text[:18],box=[85,950,500,30]),
              dict(text=text[18:],box=[85,995,200,30]),
              dict(text='刚刚·回复',box=[85,1060,140,32]),
              dict(text='删除',box=[650,1060,55,32])]
        self.assertTrue(comment_posted(rows,text))
        rows[1]['box'][1]=1030
        self.assertFalse(comment_posted(rows,text))

    def test_unrelated_words_between_wrapped_lines_do_not_match(self):
        text='化石上的啃食痕迹还能这样反推植物运动，长知识了！'
        rows=[dict(text=text[:18],box=[25,1105,500,20]),
              dict(text='其他内容',box=[25,1135,200,20]),
              dict(text=text[18:],box=[25,1165,200,20]),
              dict(text='发布',box=[630,1210,50,32])]
        self.assertIsNone(comment_draft_ready(rows,text))
