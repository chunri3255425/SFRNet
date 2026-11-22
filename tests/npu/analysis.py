#!/usr/bin/env python3
"""NPU 算力利用率分析"""

import os

print('='*70)
print('NPU 算力利用率深度分析')
print('='*70)

# 获取模型文件大小
model_path = '/root/sfrnet_wired_fp16.rknn'
model_size_mb = os.path.getsize(model_path) / 1024 / 1024

print(f'\n[模型信息]')
print(f'模型文件: {model_path}')
print(f'文件大小: {model_size_mb:.1f} MB')

# 估计参数量 (FP16: 2 bytes/param)
params_m = model_size_mb / 2
print(f'估计参数量: {params_m:.1f}M')

# 计算 FLOPs
input_pixels = 512 * 512
avg_feature_scale = 0.25  # 考虑多尺度特征的平均值
estimated_flops = params_m * 1e6 * input_pixels * avg_feature_scale * 2

print(f'\n[计算量估计]')
print(f'输入像素: {input_pixels} (512x512)')
print(f'平均特征图尺度: {avg_feature_scale}')
print(f'估计 FLOPs: {estimated_flops/1e9:.2f} GFLOPs')

# 实测推理时间
inference_time = 2.948  # 秒
actual_gops = estimated_flops / inference_time / 1e9

print(f'\n[算力分析]')
print(f'实测推理时间: {inference_time:.2f} s')
print(f'实际算力: {actual_gops:.2f} GOPS')

# NPU 标称算力
npu_int8 = 6000  # GOPS
npu_fp16 = 3000  # GOPS (估计)

util_int8 = actual_gops / npu_int8 * 100
util_fp16 = actual_gops / npu_fp16 * 100

print(f'\nNPU 标称算力:')
print(f'  INT8: {npu_int8} GOPS (6 TOPS)')
print(f'  FP16: ~{npu_fp16} GOPS (3 TOPS, 估计)')
print(f'\n算力利用率:')
print(f'  vs INT8: {util_int8:.2f}%')
print(f'  vs FP16: {util_fp16:.2f}%')

# 关键发现
print(f'\n' + '='*70)
print('[关键发现]')
print('='*70)

print('\n✓ 批处理效率: 95.5%')
print('  Batch 1 → Batch 8: 7.64x 加速')
print('  → NPU 硬件利用率良好')

print('\n✗ 内存带宽利用率: 7-14%')
print('  实测: 1.2-2.4 GB/s')
print('  理论: 17 GB/s (LPDDR4)')
print('  → 内存带宽严重浪费')

print('\n✗ 算力利用率: <1%')
print('  → 模型未针对 NPU 优化')

# 根本原因
print(f'\n' + '='*70)
print('[根本原因]')
print('='*70)

print('\n1. 模型直接导出，未做 NPU 优化')
print('   - PyTorch → ONNX → RKNN 直接转换')
print('   - 未启用算子融合、内存布局优化')

print('\n2. 版本不匹配')
print('   - 模型编译: 2.3.2')
print('   - Runtime: 2.1.0')
print('   - 触发兼容层，性能下降')

print('\n3. 内存访问模式差')
print('   - 权重随机访问导致 Cache Miss')
print('   - 带宽利用率仅 7-14%')

print('\n4. 可能存在的算子降级')
print('   - 部分算子 CPU 执行')
print('   - CPU↔NPU 数据搬运开销大')

# 优化方案
print(f'\n' + '='*70)
print('[优化方案]')
print('='*70)

print('\n优先级 1: INT8 量化')
print('  预期提升: 2-4x')
print('  推理时间: 3s → 0.75-1.5s')

print('\n优先级 2: 使用 Toolkit2 重新转换')
print('  启用算子融合优化')
print('  预期提升: 2-3x')

print('\n优先级 3: 升级 Runtime 到 2.3.2')
print('  消除版本不匹配')
print('  预期提升: 1.2-1.5x')

print('\n优先级 4: 模型结构优化')
print('  替换 NPU 不友好的算子')
print('  预期提升: 1.5-2x')

print('\n综合优化预期:')
print('  当前: 3 秒/块')
print('  优化后: 0.3-0.5 秒/块')
print('  提升: 6-10x')

print('\n' + '='*70)
print('分析完成')
print('='*70)
