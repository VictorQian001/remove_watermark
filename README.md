# 去水印工具

这是一个基于 OpenCV 的命令行去水印脚本，支持两种模式：

- `1`：去除角落水印
- `2`：去除平铺水印

## 安装依赖

```bash
python3 -m pip install -r requirements.txt
```

## 用法

```bash
python3 remove_watermark.py -i input.jpg -o output.jpg -m 1
```

```bash
python3 remove_watermark.py -i input.jpg -o output.jpg -m 2
```

## 可选参数

- `--mask-output`：输出识别到的水印掩码图
- `--corner-ratio`：仅对 `mode=1` 生效，控制四角检测范围，默认 `0.24`
- `--strength`：控制检测激进程度，默认 `1.0`
- `--corner`：仅对 `mode=1` 生效，可选 `all/top-left/top-right/bottom-left/bottom-right`
- `--roi`：仅对 `mode=1` 生效，手动指定区域 `x,y,w,h`，会优先于 `--corner`

## 示例

角落水印：

```bash
python3 remove_watermark.py \
  -i ./examples/in.jpg \
  -o ./examples/out.jpg \
  -m 1 \
  --corner bottom-right \
  --roi 1100,520,420,220 \
  --mask-output ./examples/mask.jpg
```

平铺水印：

```bash
python3 remove_watermark.py \
  -i ./examples/in.jpg \
  -o ./examples/out.jpg \
  -m 2 \
  --strength 1.2
```

## 说明

- `mode=1` 会只在角落平滑背景区域里找淡灰/半透明水印并做保守修复，默认比之前更不容易误伤人物和正文。
- `mode=2` 会在全图查找半透明重复纹理，更适合平铺文字水印。
- 如果只想修一个角，优先显式指定 `--corner bottom-right` 这类参数，避免其它角被误识别。
- 自动识别不稳时，优先直接给 `--roi x,y,w,h`，这样掩码只会在该矩形内扩张。
- 如果角落还有淡淡残影，优先尝试把 `--strength` 提高到 `1.2` 到 `1.5`，必要时配合 `--mask-output` 检查掩码是否覆盖完整。
- 不同图片的水印透明度、颜色和背景差异很大，必要时可以配合 `--mask-output` 检查掩码效果，再调 `--strength`。
