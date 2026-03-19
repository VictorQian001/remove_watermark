# 图片角落覆盖层清理工具

用于清理你自有图片中的角落文字、Logo 或其他覆盖层标记。

For the English version, see [README_EN.md](./README_EN.md).

## 功能

- `mode=1`：适合角落里的文字或 Logo 覆盖层
- `mode=2`：适合全图重复出现的浅色纹理或覆盖层

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

- `--mask-output`：输出识别到的掩码图，便于检查覆盖范围
- `--corner-ratio`：仅对 `mode=1` 生效，控制四角检测范围，默认 `0.24`
- `--strength`：控制检测激进程度，默认 `1.0`
- `--corner`：仅对 `mode=1` 生效，可选 `all/top-left/top-right/bottom-left/bottom-right`
- `--roi`：仅对 `mode=1` 生效，手动指定区域 `x,y,w,h`，会优先于 `--corner`

## 示例

角落覆盖层清理：

```bash
python3 remove_watermark.py \
  -i ./examples/in.jpg \
  -o ./examples/out.jpg \
  -m 1 \
  --corner bottom-right \
  --roi 1100,520,420,220 \
  --mask-output ./examples/mask.jpg
```

全图重复覆盖层清理：

```bash
python3 remove_watermark.py \
  -i ./examples/in.jpg \
  -o ./examples/out.jpg \
  -m 2 \
  --strength 1.2
```

## 说明

- `mode=1` 会优先处理图片角落区域内的浅色文字、Logo 或其他覆盖层标记。
- `mode=2` 会在全图查找重复出现的浅色纹理或覆盖层，更适合整张图里重复分布的干扰元素。
- 自动识别不稳时，优先直接给 `--roi x,y,w,h`，这样掩码只会在该矩形内扩张。
- 建议先用 `--mask-output` 检查掩码是否覆盖完整，再调整 `--strength`。

## 合规使用

- 仅应处理你本人拥有、授权使用，或有权编辑的图片。
- 请确保你的使用场景符合图片来源平台、服务条款及适用法律法规。
