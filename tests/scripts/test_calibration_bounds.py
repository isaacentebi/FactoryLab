"""The calibration screen states no bound the world does not enforce (defect 16).

The architect retired the leverage ceiling: the venue's refusal is the only
limit on leverage. A screening case that tells a model the world caps leverage
at 3 teaches the seat a rule the kernel no longer has.
"""

import json


def test_no_calibration_case_announces_a_leverage_ceiling():
    from scripts.calibrate_seats import case_set

    for case in case_set():
        text = json.dumps({"inputs": case.inputs, "description": case.description}).lower()
        assert "max_leverage" not in text, case.id
        assert "world_bound" not in text, case.id
