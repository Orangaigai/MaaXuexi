"""Choice questions still scroll when hints or answer options are below the fold."""
import unittest
from unittest.mock import Mock
import numpy as np
from test_optimization import worker,job


class ChoiceScrollTests(unittest.TestCase):
    def test_hidden_hint_scrolls_then_keeps_options_and_reads_hint(self):
        value=worker();value._sleep=Mock()
        value._get_options=Mock(side_effect=[{'A':('甲',[60,400,200,50])},{'B':('乙',[60,450,200,50])}])
        value._screen_records=Mock(return_value=[dict(text='单选题',box=[70,200,120,40]),dict(text='请选择正确选项',box=[60,300,350,40])])
        value._get_red_texts=Mock(return_value=['甲'])
        value.tasker.post_task.side_effect=[job(False),job(),job()]
        value.tasker.controller.post_screencap.return_value.wait.return_value.get.return_value=np.zeros((1280,720,3),np.uint8)
        images,options,hints,blanks=value._prepare('单选题')
        self.assertEqual(set(options),{'A','B'})
        self.assertEqual(hints,['甲'])
        self.assertEqual(blanks,0)
        self.assertEqual(len(images),3)
        value.tasker.controller.post_swipe.assert_called_once()
        self.assertEqual([call.args[0] for call in value.tasker.post_task.call_args_list],['查找提示','查看提示','关闭提示'])

    def test_hidden_choice_scrolls_once_and_reselects_the_same_answer(self):
        value=worker();value._sleep=Mock()
        value.tasker.post_task.side_effect=[job(False),job()]
        self.assertTrue(value._submit_answer('单选题',['A'],False))
        self.assertEqual([call.args[0] for call in value.tasker.post_task.call_args_list],['选项A','选项A'])
        value.tasker.controller.post_swipe.assert_called_once()
        self.assertTrue(200<=value.tasker.controller.post_swipe.call_args.args[-1]<=300)


if __name__=='__main__':unittest.main()
