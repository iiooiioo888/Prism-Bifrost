"""來源可信度校準。模型不參與。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CalibrationResult:
    source_type: str
    base_band_midpoint: float
    source_multiplier: float
    acquisition_multiplier: float
    bayesian_factor: float
    calibrated_confidence: float
    abstain: bool


def midpoint(band: tuple[float, float]) -> float:
    low, high = band
    if high < low:
        raise ValueError("置信帶上界低於下界")
    return (low + high) / 2


def calibrate(
    source_type: str,
    acquisition_mode: str,
    bands: dict[str, tuple[float, float]],
    multipliers: dict[str, float],
    abstain_threshold: float = 0.35,
    fallback_penalty: float = 0.90,
    bayesian_factor: float = 1.0,
) -> CalibrationResult:
    if source_type not in bands:
        raise KeyError(f"來源類型沒有基礎置信帶：{source_type}")
    if source_type not in multipliers:
        raise KeyError(f"來源類型沒有可信度乘數：{source_type}")
    if acquisition_mode not in {"live", "fallback"}:
        raise ValueError(f"未知採集模式：{acquisition_mode}")
    if bayesian_factor <= 0:
        raise ValueError("貝氏乘數必須為正")
    base = midpoint(bands[source_type])
    source_multiplier = multipliers[source_type]
    acquisition_multiplier = fallback_penalty if acquisition_mode == "fallback" else 1.0
    # 乘數只降不升：呼叫端若給出大於 1 的值，在此夾制。
    source_multiplier = min(source_multiplier, 1.0)
    acquisition_multiplier = min(acquisition_multiplier, 1.0)
    calibrated = base * source_multiplier * acquisition_multiplier * bayesian_factor
    return CalibrationResult(
        source_type=source_type,
        base_band_midpoint=base,
        source_multiplier=source_multiplier,
        acquisition_multiplier=acquisition_multiplier,
        bayesian_factor=bayesian_factor,
        calibrated_confidence=calibrated,
        abstain=calibrated < abstain_threshold,
    )
