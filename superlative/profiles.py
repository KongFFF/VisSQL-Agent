"""
Superlative 模式配置层。

这个文件只负责回答两个问题：
1. 当前正式保留的模式是什么；
2. 历史实验模式分别打开了哪些能力。

这样可以把 phase1/phase2 的历史实验兼容逻辑，从主求解文件里隔离出来。
"""

FINAL_SUPERLATIVE_MODE = "phase1_e"

LEGACY_SUPERLATIVE_MODES = {
    "v1",
    "v2",
    "phase0",
    "phase1",
    "phase1_c",
    "phase1_d",
    "phase2_a",
    "phase2_b",
}

SUPPORTED_SUPERLATIVE_MODES = {FINAL_SUPERLATIVE_MODE, *LEGACY_SUPERLATIVE_MODES}

MODES_WITH_EXCLUSION_LAYER = {
    FINAL_SUPERLATIVE_MODE,
    "v2",
    "phase0",
    "phase1",
    "phase1_c",
    "phase1_d",
    "phase2_a",
    "phase2_b",
}

MODES_WITH_PHASE1_ROUTER = {
    FINAL_SUPERLATIVE_MODE,
    "phase1",
    "phase1_c",
    "phase1_d",
    "phase2_a",
    "phase2_b",
}

MODES_WITH_CONTROLLED_SLOT_FILLING = {"phase1_c"}
MODES_WITH_ENTITY_COUNT_PLAN = {FINAL_SUPERLATIVE_MODE, "phase1_d"}
MODES_WITH_STRUCTURED_PROJECTION_SELECTOR = {FINAL_SUPERLATIVE_MODE}
MODES_WITH_UNIFIED_COUNT_PLANNER = {"phase2_a", "phase2_b"}
MODES_WITH_DECOMPOSED_COUNT_SLOTTING = {"phase2_b"}
MODES_ALLOWING_COUNT_OUTPUT = {"phase2_b"}

MODES_WITH_PROJECTION_VALIDATOR = {
    FINAL_SUPERLATIVE_MODE,
    "phase1_d",
    "phase2_a",
    "phase2_b",
}


def normalize_superlative_mode(mode):
    normalized = (mode or FINAL_SUPERLATIVE_MODE).lower()
    if normalized not in SUPPORTED_SUPERLATIVE_MODES:
        raise ValueError(
            f"Unsupported superlative mode: {mode}. "
            f"Supported modes: {sorted(SUPPORTED_SUPERLATIVE_MODES)}"
        )
    return normalized


def is_formal_superlative_mode(mode):
    return normalize_superlative_mode(mode) == FINAL_SUPERLATIVE_MODE


def is_legacy_superlative_mode(mode):
    return normalize_superlative_mode(mode) in LEGACY_SUPERLATIVE_MODES


def uses_phase0_exclusion_layer(mode):
    mode = normalize_superlative_mode(mode)
    return mode in MODES_WITH_EXCLUSION_LAYER


def uses_phase1_router(mode):
    mode = normalize_superlative_mode(mode)
    return mode in MODES_WITH_PHASE1_ROUTER


def uses_controlled_slot_filling(mode):
    mode = normalize_superlative_mode(mode)
    return mode in MODES_WITH_CONTROLLED_SLOT_FILLING


def uses_entity_count_plan_mode(mode):
    mode = normalize_superlative_mode(mode)
    return mode in MODES_WITH_ENTITY_COUNT_PLAN


def uses_structured_projection_selector(mode):
    mode = normalize_superlative_mode(mode)
    return mode in MODES_WITH_STRUCTURED_PROJECTION_SELECTOR


def uses_unified_count_planner(mode):
    mode = normalize_superlative_mode(mode)
    return mode in MODES_WITH_UNIFIED_COUNT_PLANNER


def uses_decomposed_count_slotting(mode):
    mode = normalize_superlative_mode(mode)
    return mode in MODES_WITH_DECOMPOSED_COUNT_SLOTTING


def allows_count_output_count_family(mode):
    mode = normalize_superlative_mode(mode)
    return mode in MODES_ALLOWING_COUNT_OUTPUT


def uses_projection_validator(mode):
    mode = normalize_superlative_mode(mode)
    return mode in MODES_WITH_PROJECTION_VALIDATOR
