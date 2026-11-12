#!/usr/bin/env python3
"""RK3576 推理管道性能分析脚本"""

import time
import subprocess
import numpy as np
import cv2
import sys

def test_capture(num_tests=5):
    """测试摄像头抓帧性能"""
    print("\n" + "="*60)
    print("[1] 摄像头抓帧测试 (v4l2-ctl)")
    print("="*60)
    
    times = []
    data_sizes = []
    
    for i in range(num_tests):
        t0 = time.perf_counter()
        cmd = [
            'v4l2-ctl', '--device=/dev/video0',
            '--set-fmt-video=width=1920,height=1080,pixelformat=RG10',
            '--stream-mmap=1', '--stream-count=1', '--stream-to=-'
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=10)
            t1 = time.perf_counter()
            
            if len(result.stdout) > 0:
                times.append(t1 - t0)
                data_sizes.append(len(result.stdout))
                print(f"  第{i+1}次: {(t1-t0)*1000:.1f} ms, {len(result.stdout)} bytes")
            else:
                print(f"  第{i+1}次: 失败 - {result.stderr.decode()[:50]}")
        except Exception as e:
            print(f"  第{i+1}次: 异常 - {e}")
    
    if times:
        print(f"\n  平均: {np.mean(times)*1000:.1f} ms")
        print(f"  速率: {1.0/np.mean(times):.1f} FPS")
        return np.mean(times), data_sizes[0]
    return None, None


def test_bayer_conversion(capture_time, data_size, num_tests=5):
    """测试 Bayer→RGB 转换性能"""
    print("\n" + "="*60)
    print("[2] Bayer→RGB 转换测试")
    print("="*60)
    
    # 先抓取一帧数据
    cmd = [
        'v4l2-ctl', '--device=/dev/video0',
        '--set-fmt-video=width=1920,height=1080,pixelformat=RG10',
        '--stream-mmap=1', '--stream-count=1', '--stream-to=-'
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=10)
    
    if len(result.stdout) == 0:
        print("  无法获取帧数据")
        return None
    
    raw = np.frombuffer(result.stdout, dtype=np.uint8)
    times = []
    
    for i in range(num_tests):
        t0 = time.perf_counter()
        
        # 10-bit Bayer → 16-bit → 8-bit
        raw_16 = raw.view(np.uint16).reshape((1080, 960))
        raw_8 = (raw_16 >> 2).astype(np.uint8)
        rgb = cv2.cvtColor(raw_8, cv2.COLOR_BAYER_RG2RGB)
        
        t1 = time.perf_counter()
        times.append(t1 - t0)
        print(f"  第{i+1}次: {(t1-t0)*1000:.1f} ms, 输出: {rgb.shape}")
    
    print(f"\n  平均: {np.mean(times)*1000:.1f} ms")
    return np.mean(times)


def test_preprocessing(num_tests=5):
    """测试图像预处理性能"""
    print("\n" + "="*60)
    print("[3] 图像预处理测试 (resize + normalize)")
    print("="*60)
    
    # 创建测试图像
    test_img = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)
    times = []
    
    for i in range(num_tests):
        t0 = time.perf_counter()
        
        img = cv2.resize(test_img, (512, 512))
        img = img.astype(np.float32) / 255.0
        img = (img - np.array([0.485, 0.456, 0.406])) / np.array([0.229, 0.224, 0.225])
        img = np.transpose(img, (2, 0, 1))
        img = np.expand_dims(img, axis=0).astype(np.float32)
        
        t1 = time.perf_counter()
        times.append(t1 - t0)
        print(f"  第{i+1}次: {(t1-t0)*1000:.1f} ms")
    
    print(f"\n  平均: {np.mean(times)*1000:.1f} ms")
    return np.mean(times)


def test_npu_inference(num_tests=5):
    """测试 NPU 推理性能"""
    print("\n" + "="*60)
    print("[4] NPU 推理测试 (RKNN)")
    print("="*60)
    
    try:
        from rknnlite.api import RKNNLite
        
        rknn = RKNNLite()
        
        # 加载模型
        t0 = time.perf_counter()
        ret = rknn.load_rknn('/root/sfrnet_wired_fp16.rknn')
        if ret != 0:
            print(f"  模型加载失败: {ret}")
            return None, None
        
        ret = rknn.init_runtime(core_mask=RKNNLite.NPU_CORE_0)
        if ret != 0:
            print(f"  运行时初始化失败: {ret}")
            return None, None
        t1 = time.perf_counter()
        print(f"  模型加载+初始化: {(t1-t0)*1000:.1f} ms")
        
        # 准备输入数据
        test_input = np.random.rand(1, 3, 512, 512).astype(np.float32)
        
        # 推理测试
        times = []
        for i in range(num_tests):
            t0 = time.perf_counter()
            outputs = rknn.inference(inputs=[test_input])
            t1 = time.perf_counter()
            times.append(t1 - t0)
            print(f"  第{i+1}次推理: {(t1-t0)*1000:.1f} ms")
        
        rknn.release()
        
        mean_time = np.mean(times)
        print(f"\n  平均推理时间: {mean_time:.1f} ms")
        print(f"  推理速率: {1000/mean_time:.1f} FPS (单块 512x512)")
        return mean_time, len(outputs)
        
    except Exception as e:
        print(f"  NPU 推理失败: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def test_full_pipeline(capture_time, bayer_time, preprocess_time, infer_time):
    """测试完整管道性能"""
    print("\n" + "="*60)
    print("[5] 完整管道性能预估")
    print("="*60)
    
    if not all([capture_time, bayer_time, preprocess_time, infer_time]):
        print("  缺少测试数据，无法预估")
        return
    
    # 计算单帧总时间
    single_frame_time = capture_time + bayer_time + preprocess_time + infer_time
    
    print(f"\n  单帧处理时间分解:")
    print(f"    抓帧:     {capture_time*1000:.1f} ms ({capture_time/single_frame_time*100:.1f}%)")
    print(f"    Bayer转换: {bayer_time*1000:.1f} ms ({bayer_time/single_frame_time*100:.1f}%)")
    print(f"    预处理:   {preprocess_time*1000:.1f} ms ({preprocess_time/single_frame_time*100:.1f}%)")
    print(f"    NPU推理:  {infer_time*1000:.1f} ms ({infer_time/single_frame_time*100:.1f}%)")
    print(f"    总计:     {single_frame_time*1000:.1f} ms")
    print(f"    帧率:     {1.0/single_frame_time:.2f} FPS (单块)")
    
    # 分块推理预估
    print(f"\n  分块推理预估 (1920x1080, tile=512, stride=256):")
    num_tiles_x = (1920 - 512) // 256 + 1
    num_tiles_y = (1080 - 512) // 256 + 1
    total_tiles = num_tiles_x * num_tiles_y
    
    # 抓帧只需一次
    total_time = capture_time + bayer_time + (preprocess_time + infer_time) * total_tiles
    
    print(f"    分块数: {num_tiles_x} x {num_tiles_y} = {total_tiles} 块")
    print(f"    预计总时间: {total_time:.2f} 秒")
    print(f"    等效帧率: {1.0/total_time:.3f} FPS")


def main():
    print("="*60)
    print("RK3576 推理管道性能分析")
    print("="*60)
    
    # 运行各项测试
    capture_time, data_size = test_capture()
    bayer_time = test_bayer_conversion(capture_time, data_size)
    preprocess_time = test_preprocessing()
    infer_time, num_outputs = test_npu_inference()
    
    # 综合分析
    test_full_pipeline(capture_time, bayer_time, preprocess_time, infer_time)
    
    print("\n" + "="*60)
    print("性能分析完成")
    print("="*60)


if __name__ == '__main__':
    main()
