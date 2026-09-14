"""Pool-only turnover rotation. Turnover is activity, NOT net cash inflow."""
from collections import defaultdict
import math
import statistics


def finite(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def attach_rotation(rows, sectors):
    groups = defaultdict(list)
    for row in rows:
        name = sectors.get(row["stock_id"], {}).get("industry_category")
        if name:
            groups[name].append(row)
    total5 = sum(r["entry_metrics"].get("average_turnover_5_twd", 0) for r in rows)
    total20 = sum(r["entry_metrics"].get("average_turnover_20_twd", 0) for r in rows)
    states = {}
    for name, members in groups.items():
        if len(members) < 3 or not total5 or not total20:
            continue
        money5 = sum(r["entry_metrics"].get("average_turnover_5_twd", 0) for r in members)
        money20 = sum(r["entry_metrics"].get("average_turnover_20_twd", 0) for r in members)
        if not money20:
            continue
        change = (money5/total5-money20/total20)*100
        strength = statistics.mean(r["features"][2] for r in members)
        breadth = statistics.mean(r["features"][0]>0 for r in members)*100
        state = ("熱度增加且相對轉強" if change>0 and strength>0
                 else "熱度增加但價格未確認" if change>0
                 else "相對強勢但熱度減少" if strength>0 else "熱度與相對強度偏弱")
        states[name] = {"industry": name, "members": len(members),
                        "share_5_pct": round(money5/total5*100, 3),
                        "share_change_pp": change, "relative_return_20d": strength,
                        "positive_breadth_pct": breadth, "state": state}
    for row in rows:
        metrics = row["entry_metrics"]
        name = sectors.get(row["stock_id"], {}).get("industry_category", "分類未知")
        sector = states.get(name)
        row["rotation"] = sector or {"industry": name, "state": "分類或同類股樣本不足"}
        row["rotation_context"] = [
            finite(metrics.get("capital_flow_5d_pct")),
            finite(metrics.get("capital_flow_20d_pct")),
            finite(metrics.get("turnover_acceleration_5v20")),
            sector["share_change_pp"] if sector else None,
            sector["relative_return_20d"] if sector else None,
            sector["positive_breadth_pct"] if sector else None,
        ]
    return sorted(states.values(), key=lambda s: (-s["share_change_pp"], s["industry"]))


def similarity(left, right):
    # Fixed, disclosed bandwidths, not performance-optimised factor bonuses.
    scales = (50, 50, 1, 2, 10, 50)
    distances = [min(5, abs(a-b)/s) for a,b,s in zip(left or [], right or [], scales)
                 if a is not None and b is not None]
    return 1/(1+sum(distances)/len(distances)) if distances else 1.0


def normalize_institutions(rows):
    days = defaultdict(dict)
    for row in rows:
        if row.get("name") not in ("Foreign_Investor", "Investment_Trust"):
            continue
        buy, sell = finite(row.get("buy")), finite(row.get("sell"))
        if buy is None or sell is None or min(buy, sell)<0:
            continue
        days[str(row["date"])[:10]][row["name"]] = buy-sell
    return [{"date": d, "foreign_net_shares": v["Foreign_Investor"],
             "trust_net_shares": v["Investment_Trust"]}
            for d,v in sorted(days.items())
            if "Foreign_Investor" in v and "Investment_Trust" in v]


def institution_summary(rows, dates, volume):
    by_date = {r["date"]: r for r in rows}
    if len(dates)!=5 or any(d not in by_date for d in dates) or volume<=0:
        return {"status": "缺少對齊的5日外資投信資料", "net_volume_pct": None}
    foreign = sum(by_date[d]["foreign_net_shares"] for d in dates)
    trust = sum(by_date[d]["trust_net_shares"] for d in dates)
    return {"status": "外資＋投信，展示觀察、尚未納入機率訓練",
            "foreign_net_shares": foreign, "trust_net_shares": trust,
            "net_volume_pct": (foreign+trust)/volume*100,
            "as_of_date": dates[-1]}
