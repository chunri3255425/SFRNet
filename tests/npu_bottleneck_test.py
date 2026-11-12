#!/usr/bin/env python3
"""RK3576 NPU 性能瓶颈深度分析脚本"""

import time
import numpy as np
from rknnlite.api import RKNNLite

def test_continuous_inference():
    """测试 1: 连续推理稳定性"""
    print('='*70)
    print('[测试 1] 连续推理测试 (排除首次冷启动)')
    print('='*70)
    
    rknn = RKNNLite()
    rknn.load_rknn('/root/sfrnet_wired_fp16.rknn')
    rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0)
    
    # 预热
    test_input = np.random.rand(1, 3, 512, 512).astype(np.float32)
    rknn.inference(inputs=[test_input])
    
    # 连续测试 20 次
    times = []
    for i in range(20):
        t0 = time.perf_counter()
        rknn.inference(inputs=[test_input])
        t1 = time.perf_counter()
        times.append(t1 - t0)
    
    rknn.release()
    
    print(f'测试次数: {len(times)}')
    print(f'平均推理时间: {np.mean(times)*1000:.2f} ms')
    print(f'标准差: {np.std(times)*1000:.2f} ms')
    print(f'最小值: {np.min(times)*1000:.2f} ms')
    print(f'最大值: {np.max(times)*1000:.2f} ms')
    print(f'变异系数: {np.std(times)/np.mean(times)*100:.2f}%')
    
    return np.mean(times)


def test_input_size_scaling():
    """测试 2: 不同输入尺寸对比"""
    print('\n' + '='*70)
    print('[测试 2] 不同输入尺寸对比 (检测内存带宽影响)')
    print('='*70)
    
    rknn = RKNNLite()
    rknn.load_rknn('/root/sfrnet_wired_fp16.rknn')
    rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0)
    
    sizes = [(1, 3, 256, 256), (1, 3, 384, 384), (1, 3, 512, 512)]
    results = []
    
    for size in sizes:
        test_data = np.random.rand(*size).astype(np.float32)
        input_size_mb = test_data.nbytes / 1024 / 1024
        
        # 预热
        rknn.inference(inputs=[test_data])
        
        # 测试 5 次
        times_size = []
        for i in range(5):
            t0 = time.perf_counter()
            rknn.inference(inputs=[test_data])
            t1 = time.perf_counter()
            times_size.append(t1 - t0)
        
        avg_time = np.mean(times_size)
        results.append((size, input_size_mb, avg_time))
        print(f'输入尺寸 {size}: {input_size_mb:.2f} MB, 推理时间: {avg_time*1000:.2f} ms')
    
    rknn.release()
    return results


def test_data_copy_overhead():
    """测试 3: 数据拷贝开销"""
    print('\n' + '='*70)
    print('[测试 3] 数据拷贝开销测试')
    print('='*70)
    
    test_arrays = []
    for size in [(1, 3, 256, 256), (1, 3, 512, 512)]:
        arr = np.random.rand(*size).astype(np.float32)
        test_arrays.append(arr)
    
    times_copy = []
    for arr in test_arrays:
        t0 = time.perf_counter()
        arr_copy = arr.copy()
        t1 = time.perf_counter()
        times_copy.append(t1 - t0)
        print(f'数据拷贝 {arr.nbytes/1024/1024:.2f} MB: {(t1-t0)*1000:.3f} ms')
    
    return times_copy


def analyze_bottleneck(results, times_copy, base_infer_time):
    """分析瓶颈类型"""
    print('\n' + '='*70)
    print('[分析结果] 瓶颈类型判断')
    print('='*70)
    
    time_256 = results[0][3]
    time_512 = results[2][3]
    size_ratio = (512*512) / (256*256)  # 4x
    time_ratio = time_512 / time_256
    
    print(f'\n输入尺寸增长倍数: {size_ratio:.1f}x')
    print(f'推理时间增长倍数: {time_ratio:.2f}x')
    
    # 判断瓶颈类型
    if time_ratio < 1.2:
        print(f'\n✓ 推理时间几乎不随输入尺寸变化 → **计算瓶颈**')
        print(f'  - NPU 计算单元饱和')
        print(f'  - 优化方向：模型量化、剪枝、使用更轻量模型')
        bottleneck_type = 'compute'
    elif time_ratio > size_ratio * 0.8:
        print(f'\n✓ 推理时间随输入尺寸线性增长 → **内存带宽瓶颈**')
        print(f'  - 数据传输成为主要延迟')
        print(f'  - 优化方向：减少数据搬运、模型压缩、INT8量化')
        bottleneck_type = 'memory'
    else:
        print(f'\n✓ 推理时间增长介于两者之间 → **混合瓶颈**')
        print(f'  - 计算和内存都有影响')
        print(f'  - 优化方向：综合考虑')
        bottleneck_type = 'mixed'
    
    # 数据拷贝占比
    copy_time_512 = times_copy[1]
    copy_ratio = copy_time_512 / base_infer_time * 100
    print(f'\n数据拷贝时间占比: {copy_ratio:.2f}%')
    
    if copy_ratio > 10:
        print(f'⚠ 数据拷贝开销显著 (>10%)')
        print(f'  - 建议：使用零拷贝、输入缓冲区复用')
    else:
        print(f'✓ 数据拷贝开销可接受 (<10%)')
    
    # 计算理论分析
    print('\n' + '-'*70)
    print('[理论分析] RK3576 NPU 性能评估')
    print('-'*70)
    
    # RK3576 NPU 规格
    npu_tops = 6  # 6 TOPS
    npu_ops = npu_tops * 1e12  # 每秒操作数
    
    # 估计模型计算量 (假设 SFRNet ~10 GFLOPs for 512x512)
    estimated_flops_g = 10
    estimated_flops = estimated_flops_g * 1e9
    
    # 理论最优推理时间
    theoretical_time = estimated_flops / npu_ops
    print(f'\n估计模型 FLOPs: {estimated_flops_g} GFLOPs')
    print(f'NPU 理论算力: {npu_tops} TOPS')
    print(f'理论最优推理时间: {theoretical_time*1000:.2f} ms')
    print(f'实测推理时间: {base_infer_time*1000:.2f} ms')
    print(f'算力利用率: {theoretical_time/base_infer_time*100:.2f}%')
    
    # 内存带宽分析
    print('\n' + '-'*70)
    print('[内存带宽分析]')
    print('-'*70)
    
    # RK3576 内存带宽约 17 GB/s (LPDDR4/4X)
    memory_bandwidth_gbs = 17
    
    # 输入数据量
    input_size_mb = results[2][1]  # 512x512
    input_size_bytes = input_size_mb * 1024 * 1024
    
    # 理论数据传输时间
    transfer_time = input_size_bytes / (memory_bandwidth_gbs * 1e9)
    print(f'\n输入数据量: {input_size_mb:.2f} MB')
    print(f'内存带宽: {memory_bandwidth_gbs} GB/s')
    print(f'理论传输时间: {transfer_time*1000:.3f} ms')
    print(f'实测推理时间: {base_infer_time*1000:.2f} ms')
    print(f'传输时间占比: {transfer_time/base_infer_time*100:.2f}%')
    
    if transfer_time / base_infer_time > 0.3:
        print(f'\n⚠ 内存传输时间占比 >30%，存在内存带宽瓶颈')
    else:
        print(f'\n✓ 内存传输时间占比 <30%，主要是计算瓶颈')
    
    return bottleneck_type


def main():
    print('='*70)
    print('RK3576 NPU 性能瓶颈深度分析')
    print('='*70)
    
    # 运行测试
    base_infer_time = test_continuous_inference()
    results = test_input_size_scaling()
    times_copy = test_data_copy_overhead()
    
    # 分析瓶颈
    bottleneck_type = analyze_bottleneck(results, times_copy, base_infer_time)
    
    # 输出优化建议
    print('\n' + '='*70)
    print('[优化建议]')
    print('='*70)
    
    if bottleneck_type == 'compute':
        print('\n主要瓶颈：NPU 计算能力')
        print('推荐优化方案：')
        print('  1. 模型量化 (FP16 → INT8): 加速 2-4x')
        print('  2. 模型剪枝: 减少冗余计算')
        print('  3. 使用更轻量模型: MobileNet/ShuffleNet 骨干')
        print('  4. 降低输入分辨率: 减少计算量')
    elif bottleneck_type == 'memory':
        print('\n主要瓶颈：内存带宽')
        print('推荐优化方案：')
        print('  1. 模型压缩: 减少参数和中间激活')
        print('  2. 零拷贝优化: 避免数据搬运')
        print('  3. 算子融合: 减少中间结果')
        print('  4. INT8 量化: 减少 50% 内存访问')
    else:
        print('\n主要瓶颈：计算 + 内存混合')
        print('推荐优化方案：')
        print('  1. INT8 量化: 同时优化计算和内存')
        print('  2. 模型架构搜索 (NAS): 针对硬件优化')
        print('  3. 分块推理优化: 减少单次处理数据量')
    
    print('\n' + '='*70)
    print('分析完成')
    print('='*70)


if __name__ == '__main__':
    main()
