"""生成页面图像视觉识别版 Gold 候选审核材料。"""

from pathlib import Path

import structlog

from smart_compliance.evaluation.visual_gold import build_visual_gold_artifacts


def main() -> None:
    """从项目内视觉转录数据生成审核材料。"""
    project_root = Path(__file__).resolve().parents[1]
    outputs = build_visual_gold_artifacts(
        transcription_path=project_root / "data" / "annotations" / "visual-reference-transcription-v1.json",
        project_root=project_root,
        output_dir=project_root / "outputs" / "qa" / "gold-visual-v1",
    )
    structlog.get_logger().info("visual_gold_artifacts_built", files=[str(path) for path in outputs])


if __name__ == "__main__":
    main()
