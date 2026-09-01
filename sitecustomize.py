"""Load the fixed largo-target3-v1 gate on the historical analysis branch."""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Any, Mapping
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
try:
    import largo_material_0906 as module
except Exception:
    module = None

if module is not None and not hasattr(module, "target3_gate"):
    def target3_gate(scored: Mapping[str, Any], entry: Mapping[str, Any]) -> dict[str, Any]:
        theme = scored.get("theme") if isinstance(scored.get("theme"), Mapping) else {}
        evidence = scored.get("evidence") if isinstance(scored.get("evidence"), Mapping) else {}
        structure = scored.get("structure") if isinstance(scored.get("structure"), Mapping) else {}
        ask = module.num(entry.get("entry_ask")); bid = module.num(entry.get("entry_bid"))
        spread_pct = ((ask - bid) / ask * 100.0) if ask and bid and 0 < bid <= ask else None
        breadth = module.num(theme.get("breadth")); rank = module.num(theme.get("leader_rank"))
        directness = module.num(evidence.get("directness_points")); freshness = module.num(evidence.get("freshness_points"))
        close_location = module.num(structure.get("close_location")); pattern_score = module.num(structure.get("pattern_score")); risk = module.num(structure.get("risk_rate"))
        hard_reject = bool(scored.get("hard_reject"))
        common = {
            "hard_exclusion_clear": not hard_reject,
            "entry_quote_known": ask is not None and bid is not None and spread_pct is not None,
            "pattern_60_to_89": pattern_score is not None and 60 <= pattern_score < 90,
            "structural_risk_at_most_8pct": risk is not None and risk <= 0.08,
        }
        theme_checks = {**common,
            "theme_breadth_85_to_90pct": breadth is not None and 0.85 <= breadth < 0.90,
            "theme_rank_at_most_4": rank is not None and rank <= 4,
            "close_location_at_least_75pct": close_location is not None and close_location >= 0.75,
            "spread_at_most_0_20pct": spread_pct is not None and spread_pct <= 0.20,
        }
        direct_checks = {**common,
            "directness_at_least_14": directness is not None and directness >= 14,
            "freshness_at_least_3": freshness is not None and freshness >= 3,
            "spread_at_most_0_10pct": spread_pct is not None and spread_pct <= 0.10,
        }
        theme_pass = all(theme_checks.values()); direct_pass = all(direct_checks.values())
        lane = "THEME_AND_DIRECT" if theme_pass and direct_pass else "THEME_CONTINUATION" if theme_pass else "DIRECT_EVENT" if direct_pass else "NONE"
        if hard_reject: status = "BLOCK"
        elif lane != "NONE": status = "PASS"
        else: status = "WATCH" if max(sum(theme_checks.values()) / len(theme_checks), sum(direct_checks.values()) / len(direct_checks)) >= 0.70 else "NONE"
        size_band = "NO_POSITION" if risk is None or risk > 0.08 else "BASE" if risk <= 0.04 else "HALF" if risk <= 0.06 else "QUARTER"
        if lane == "THEME_CONTINUATION": blockers = [k for k,v in theme_checks.items() if not v]
        elif lane == "DIRECT_EVENT": blockers = [k for k,v in direct_checks.items() if not v]
        elif lane == "THEME_AND_DIRECT": blockers = []
        else:
            a=[k for k,v in theme_checks.items() if not v]; b=[k for k,v in direct_checks.items() if not v]; blockers=a if len(a)<=len(b) else b
        return {"version":"largo-target3-v1","target_pct":3.0,"research_only":True,"status":status,"eligible":status=="PASS","lane":lane,"spread_pct":None if spread_pct is None else round(spread_pct,4),"size_band":size_band,"theme_checks":theme_checks,"direct_checks":direct_checks,"theme_pass_count":sum(theme_checks.values()),"theme_check_count":len(theme_checks),"direct_pass_count":sum(direct_checks.values()),"direct_check_count":len(direct_checks),"blockers":blockers,"note":"표본 내 발굴 규칙입니다. 고정 전진검증 전에는 매수 신호로 사용하지 않습니다."}
    module.target3_gate = target3_gate
