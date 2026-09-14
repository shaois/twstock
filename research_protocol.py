"""Chronological adaptation and immutable prospective research protocol.

Training/tuning/calibration records must precede the predicted signal. Actual
prospective observations are never used to refit a frozen experiment.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
import hashlib
import math
import statistics

SCHEMA_VERSION = 1
MIN_ADAPTATION_PERIODS = 8
MIN_REPORT_PERIODS = 12
SHRINKAGE_CANDIDATES = (0.0, 0.25, 0.5, 0.75, 1.0)


def _mean_by_period(records, loss):
    groups = defaultdict(list)
    for r in records:
        groups[r["date"]].append(loss(r))
    return statistics.mean(statistics.mean(g) for g in groups.values())


def _logit(p):
    p = min(.9999, max(.0001, p))
    return math.log(p/(1-p))


def _sigmoid(x):
    return 1/(1+math.exp(-max(-35, min(35, x))))


def _fit_platt(records, probability, outcome):
    """Monotone two-parameter calibration, equal weight per time period."""
    dates = {r["date"] for r in records}
    counts = {d: sum(r["date"] == d for r in records) for d in dates}
    if len({r[outcome] for r in records}) < 2:
        return {"slope": 1.0, "intercept": 0.0, "status": "one_class_identity"}
    points = [(_logit(r[probability]/100), r[outcome],
               1/(len(dates)*counts[r["date"]])) for r in records]
    a, b = 1.0, 0.0
    # Fixed regularisation against an identity map avoids unconstrained
    # calibration slopes. Evaluation data do not choose this constant.
    for _ in range(240):
        ga, gb = .02*(a-1), .02*b
        for x, y, w in points:
            residual = (_sigmoid(a*x+b)-y)*w
            ga += residual*x
            gb += residual
        a = max(0.0, min(5.0, a-.08*ga))
        b = max(-5.0, min(5.0, b-.08*gb))
    return {"slope": a, "intercept": b, "status": "fitted_on_separate_periods"}


def identity_adaptation():
    return {
        "return_shrinkage": 0.0, "alpha_shrinkage": 0.0,
        "profit_map": {"slope": 1.0, "intercept": 0.0, "status": "warmup"},
        "outperform_map": {"slope": 1.0, "intercept": 0.0, "status": "warmup"},
        "status": "insufficient_separate_periods_raw_estimate",
        "tuning_dates": [], "calibration_dates": [], "max_label_end": None,
    }


def fit_adaptation(records, signal_date):
    mature = [r for r in records if r.get("label_end_date", "9999") < signal_date]
    dates = sorted({r["date"] for r in mature})[-16:]
    if len(dates) < MIN_ADAPTATION_PERIODS:
        return identity_adaptation()
    # Chronological split: earlier OOF periods tune shrinkage; later OOF
    # periods calibrate probabilities. Neither contains current test labels.
    split = len(dates)//2
    tuning_dates, calibration_dates = dates[:split], dates[split:]
    # Purge tuning outcomes that overlap the first calibration signal.
    tune = [r for r in mature if r["date"] in tuning_dates
            and r["label_end_date"] < calibration_dates[0]]
    calibrate = [r for r in mature if r["date"] in calibration_dates]
    if len({r["date"] for r in tune}) < 3 or len({r["date"] for r in calibrate}) < 4:
        return identity_adaptation()
    def choose(local_key, prior_key, actual_key):
        losses = {
            weight: _mean_by_period(tune, lambda r:
                abs((1-weight)*r[local_key]+weight*r[prior_key]-r[actual_key]))
            for weight in SHRINKAGE_CANDIDATES
        }
        selected = min(losses, key=lambda w: (losses[w], w))
        return selected, {str(k): round(v, 6) for k, v in losses.items()}
    return_weight, return_losses = choose("local_return", "prior_return", "gross_return")
    alpha_weight, alpha_losses = choose("local_alpha", "prior_alpha", "alpha")
    return {
        "return_shrinkage": return_weight, "alpha_shrinkage": alpha_weight,
        "return_tuning_mae": return_losses, "alpha_tuning_mae": alpha_losses,
        "profit_map": _fit_platt(calibrate, "raw_profit", "won"),
        "outperform_map": _fit_platt(calibrate, "raw_outperform", "beat"),
        "status": "time_split_fitted_pending_prospective_evaluation",
        "tuning_dates": sorted({r["date"] for r in tune}),
        "calibration_dates": calibration_dates,
        "max_label_end": max(r["label_end_date"] for r in tune+calibrate),
        "selection_metric": "period_balanced_MAE",
        "probability_method": "monotone_regularized_Platt_period_balanced",
    }


def calibrated_probability(raw_percent, mapping):
    if mapping.get("status") in {"warmup", "one_class_identity"}:
        return raw_percent
    return _sigmoid(mapping["slope"]*_logit(raw_percent/100)+mapping["intercept"])*100


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ensure_protocol(existing, universe_ids, algorithm_hash, model_name, observed_at):
    """Never backdate membership; a changed pool or algorithm starts a new run."""
    state = deepcopy(existing or {})
    if state and state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported research protocol schema; preserve file for review")
    state.setdefault("schema_version", SCHEMA_VERSION)
    state.setdefault("membership_history", [])
    state.setdefault("experiments", {})
    members = sorted(universe_ids)
    membership_hash = _digest(",".join(members))
    if not state["membership_history"] or state["membership_history"][-1]["fingerprint"] != membership_hash:
        state["membership_history"].append({
            "observed_at": observed_at.isoformat(), "members": members,
            "fingerprint": membership_hash, "source": "observed_configured_universe",
            "historical_backfill_verified": False,
        })
    current = state["experiments"].get(state.get("active_experiment"))
    if not current or current["algorithm_hash"] != algorithm_hash or current["universe_fingerprint"] != membership_hash:
        identity = _digest(algorithm_hash+membership_hash+observed_at.isoformat())[:20]
        state["active_experiment"] = identity
        state["experiments"][identity] = {
            "id": identity, "model_name": model_name,
            "algorithm_hash": algorithm_hash, "universe_fingerprint": membership_hash,
            "members": members, "registered_at": observed_at.isoformat(),
            "membership_observed_at": state["membership_history"][-1]["observed_at"],
            "frozen_reference": None,
            "policy": "frozen_training_and_adaptation_no_test_label_refit",
        }
    return state


def freeze_reference(state, cohort, adaptation, training_cutoff, frozen_at=None):
    active = state["experiments"][state["active_experiment"]]
    if (active["frozen_reference"] is None and cohort
            and adaptation.get("status") == "time_split_fitted_pending_prospective_evaluation"):
        if any(r["label_end_date"] >= training_cutoff for r in cohort):
            raise ValueError("Frozen training includes unmatured labels")
        if adaptation.get("max_label_end") and adaptation["max_label_end"] >= training_cutoff:
            raise ValueError("Adaptation uses future labels")
        active["frozen_reference"] = {
            "cohort": deepcopy(cohort), "adaptation": deepcopy(adaptation),
            "training_cutoff": training_cutoff,
            "frozen_at": (frozen_at or datetime.now().astimezone()).isoformat(),
        }
    return state


def iter_snapshots(log):
    for d, snapshot in (log or {}).items():
        while isinstance(snapshot, dict):
            yield d, snapshot
            snapshot = snapshot.get("previous_version_snapshot")


def prospective_report(log, experiment):
    """Report pre-recorded, frozen, non-overlapping cohorts only.

    Missing members/outcomes are explicitly excluded whole-cohort, never
    silently reweighted. Daily overlapping snapshots remain in the audit log.
    """
    members = set(experiment["members"])
    ref = experiment.get("frozen_reference")
    selected, excluded = [], []
    reserved_exit = ""
    all_snapshots = sorted((d, s) for d, s in iter_snapshots(log)
                           if isinstance(s, dict) and s.get("experiment_id") == experiment["id"])
    for d, snapshot in all_snapshots:
        if d < reserved_exit:
            continue
        # Membership, rank and estimates were frozen together at publication.
        if set(snapshot.get("universe_members", [])) != members:
            excluded.append({"date": d, "reason": "membership_mismatch"})
            continue
        picks = snapshot.get("20d", [])
        if not picks:
            continue
        # Select signal cohorts before looking at performance or missingness.
        # A known 20-session end schedule is required for non-overlap counting.
        scheduled_exit = snapshot.get("scheduled_exit_date")
        if not scheduled_exit:
            excluded.append({"date": d, "reason": "waiting_for_20_session_calendar"})
            break
        reserved_exit = scheduled_exit
        if any(p.get("evaluation_status") != "completed" for p in picks):
            excluded.append({"date": d, "reason": "pending_late_or_missing_outcomes",
                             "ranked": len(picks)})
            continue
        published = datetime.fromisoformat(snapshot["first_recorded_at"])
        registered = datetime.fromisoformat(experiment["registered_at"])
        member_seen = datetime.fromisoformat(experiment["membership_observed_at"])
        entry_time = datetime.fromisoformat(picks[0]["entry_date"]+"T09:00:00+08:00")
        if not (registered <= published < entry_time and member_seen <= published):
            excluded.append({"date": d, "reason": "not_preregistered_before_entry"})
            continue
        if (ref is None or ref["training_cutoff"] > d
                or snapshot.get("reference_mode") != "frozen_prospective"
                or datetime.fromisoformat(ref["frozen_at"]) > published):
            excluded.append({"date": d, "reason": "training_cutoff_after_signal"})
            continue
        n = len(picks)
        def won(p):
            return p.get("actual_won", int(p["actual_net_return"]>0))
        def beat(p):
            return p.get("actual_beat", int(p["actual_alpha"]>0))
        brier = statistics.mean((p["net_profit_probability"]/100-won(p))**2 for p in picks)
        raw_brier = statistics.mean((p["raw_net_profit_probability"]/100-won(p))**2 for p in picks)
        benchmark_brier = statistics.mean((p["training_base_profit"]/100-won(p))**2 for p in picks)
        alpha_brier = statistics.mean((p["outperform_probability"]/100-beat(p))**2 for p in picks)
        sorted_picks = sorted(picks, key=lambda p: p["probability_rank"])
        groups = []
        for q in range(5):
            group = [p for i, p in enumerate(sorted_picks) if min(4, i*5//n) == q]
            groups.append({
                "quintile": q+1, "count": len(group),
                "net_return": statistics.mean(p["actual_net_return"] for p in group) if group else None,
                "alpha": statistics.mean(p["actual_alpha"] for p in group) if group else None,
            })
        selected.append({
            "date": d, "exit_date": scheduled_exit, "count": n,
            "pool_count": len(members), "unavailable_at_signal": len(members)-n,
            "profit_brier": brier, "raw_profit_brier": raw_brier,
            "training_baseline_brier": benchmark_brier, "outperform_brier": alpha_brier,
            "quintiles": groups,
            "mean_calibration_error_pp": statistics.mean(
                p["net_profit_probability"]-100*won(p) for p in picks),
        })
    summaries = []
    for q in range(5):
        groups = [r["quintiles"][q] for r in selected if r["quintiles"][q]["count"]]
        summaries.append({
            "quintile": q+1, "complete_periods": len(groups),
            "average_net_return": round(statistics.mean(g["net_return"] for g in groups), 2) if groups else None,
            "average_alpha": round(statistics.mean(g["alpha"] for g in groups), 2) if groups else None,
        })
    return {
        "experiment_id": experiment["id"], "registered_at": experiment["registered_at"],
        "frozen_reference_ready": ref is not None,
        "completed_nonoverlapping_periods": len(selected),
        "reporting_minimum_periods": MIN_REPORT_PERIODS,
        "status": ("accumulating_pre_registered_results" if len(selected)<MIN_REPORT_PERIODS
                   else "descriptive_evidence_available_not_automatic_pass"),
        "period_details": selected, "excluded_or_pending": excluded,
        "quintile_results": summaries,
        "profit_brier": round(statistics.mean(r["profit_brier"] for r in selected), 6) if selected else None,
        "raw_profit_brier": round(statistics.mean(r["raw_profit_brier"] for r in selected), 6) if selected else None,
        "training_baseline_brier": round(statistics.mean(r["training_baseline_brier"] for r in selected), 6) if selected else None,
        "test_data_used_for_training": False,
        "historical_pool_bias_removed": False,
        "scope": "prospective_fixed_pool_known_before_entry",
        "note": "首次執行以前的股票池來源仍未知；獨立指用途分離，非宣稱各期統計獨立或一定有獲利能力",
    }
