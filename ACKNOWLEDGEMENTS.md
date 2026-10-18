# 来源与致谢

本项目基于 [ravizhan/MaaXuexi](https://github.com/ravizhan/MaaXuexi) v1.7.1 修改，初始参考提交为 `ff6a0d5a811c5fc987a0c0ae77ea9abaf55fa073`。保留上游 [LICENSE](LICENSE)，项目继续使用 GNU AGPL-3.0。公开仓库维护账号为 [Orangaigai](https://github.com/Orangaigai)。

自动化运行依赖 [MaaFramework](https://github.com/MaaXYZ/MaaFramework)，使用其设备控制、识别资源和任务框架；Python依赖由 `backend/requirements-runtime.lock.txt` 记录。第三方软件及资源的原许可证仍适用，本项目不将它们改为其他许可证。

当前改进包括本地控制台、锁定依赖、DPAPI密钥配置、Windows计划、核分与续做、严格提示匹配、题面分流、订阅清理、范围受限的短评及运行诊断。详细模块见[模块说明](docs/architecture.md)。

界面与文档整理参考：

- [Primer复选框分组](https://primer.style/product/components/checkbox-group/)与[可访问性说明](https://primer.style/product/components/checkbox-group/accessibility/)：使用原生选择控件、明确标签和保存动作，不为每项叠加重边框。
- [Carbon按钮规范](https://carbondesignsystem.com/components/button/usage/)及[社区按钮层级讨论](https://github.com/carbon-design-system/carbon/issues/3509)：区分主要与辅助操作。
- [GitHub仓库最佳实践](https://docs.github.com/en/repositories/creating-and-managing-repositories/best-practices-for-repositories)和[社区README讨论](https://github.com/orgs/community/discussions/177551)：提供快速开始、依赖、配置、使用和清晰的文档入口。

这些设计资料和社区讨论用于借鉴，不代表其维护者参与、认证或推荐本项目。
