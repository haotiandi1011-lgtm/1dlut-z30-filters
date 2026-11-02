"""z30fit —— 尼康 Z30 直出滤镜的离线 1D LUT 拟合管线（Phase 1）。

模块划分：
    color    sRGB / Lab 转换与 CIEDE2000 色差
    curve    单调三次样条（Fritsch–Carlson）、保序回归、量化、断层检测
    cube     .cube LUT 读写（1D / 3D）与三线性插值
    np3      NP3 文件解析、内嵌曲线提取、512 字节 LUT 段打包
    fit1d    3D→1D 投影与拟合策略择优
    metrics  偏差指标计算
    images   合成测试图与视觉对比图渲染
    report   markdown 报告生成
"""

__version__ = "0.2.0"

LUT_POINTS = 256          # Z30 机身曲线的采样点数
LUT_MAX = 32767           # 15 位值域上限（0x7FFF）
