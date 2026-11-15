"""C2N 统一内核的测试包。

分两类：

* **配置层**（``test_config``）：不需要 CoolProp，任何环境都能跑；
* **物理层**（``test_physics`` / ``test_design_*`` / ``test_reference_*``）：
  需要 CoolProp，跑的是数值回归与物理不变量。

``python tests/run_tests.py`` 会按组运行；不带参数就全跑。
"""
