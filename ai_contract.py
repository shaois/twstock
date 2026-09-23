"""V94: no free-form numeric claims, thresholds or invented references."""
VERSION = "opinion-v94"
REFS = [f"F{i}" for i in range(1, 11)] + [f"M{i}" for i in range(1, 6)]
DECISIONS = ["可考慮買進", "等待", "避開", "資料不足"]
WATCH = ["價格趨勢", "法人方向", "量價代理", "產業相對強弱", "模型不確定性"]
SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {
              "decision": {"type": "string", "enum": DECISIONS},
              "focus_refs": {"type": "array", "items": {"type": "string", "enum": REFS}},
              "watch": {"type": "array", "items": {"type": "string", "enum": WATCH}}},
          "required": ["decision", "focus_refs", "watch"]}
