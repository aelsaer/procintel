"""Risk and anomaly indicators (spec §28).

The spec is explicit that this product surfaces *procurement patterns*, not
accusations: "Το προϊόν μπορεί να προσφέρει procurement indicators, όχι
κατηγορίες περί παρανομίας" — every indicator instance carries the
non-accusatory §28 UI copy ("Εντοπίστηκε ασυνήθιστο μοτίβο που απαιτεί
περαιτέρω εξέταση"), never a stronger claim, and §28 requires each instance
to disclose its mathematical definition, benchmark, minimum sample size,
confidence, sources and known limitations — this module computes all of that
per instance rather than a bare flag.

All twelve indicative types are implemented. Threshold-based indicators use
``PROCINTEL_PROCUREMENT_THRESHOLDS_EUR`` as an explicit operational
configuration; defaults are benchmarks for screening, not a statement of
the legally applicable threshold for any individual procedure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

NON_ACCUSATORY_MESSAGE = "Εντοπίστηκε ασυνήθιστο μοτίβο που απαιτεί περαιτέρω εξέταση."

_INACTIVE_STATUSES = ("SUSPENDED", "IN_LIQUIDATION", "DISSOLVED", "DEREGISTERED", "MERGED")


@dataclass(frozen=True)
class RiskIndicatorInstance:
    indicator_type: str
    message: str
    subject: dict[str, Any]
    value: Decimal | int | float
    benchmark: Decimal | int | float
    minimum_sample: int
    sample_size: int
    confidence: str  # HIGH | MEDIUM | LOW
    sources: list[str]
    calculated_at: datetime
    limitations: str
    definition: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _confidence_from_sample(sample_size: int, *, high: int, medium: int) -> str:
    if sample_size >= high:
        return "HIGH"
    if sample_size >= medium:
        return "MEDIUM"
    return "LOW"


def procurement_thresholds() -> list[Decimal]:
    raw = os.environ.get(
        "PROCINTEL_PROCUREMENT_THRESHOLDS_EUR",
        "30000,60000,140000,221000,443000,5382000",
    )
    values: list[Decimal] = []
    for item in raw.split(","):
        try:
            value = Decimal(item.strip())
        except Exception:
            continue
        if value > 0:
            values.append(value)
    return sorted(set(values))


async def high_buyer_concentration(
    conn: AsyncConnection, *, benchmark: Decimal = Decimal("0.70"), minimum_contracts: int = 5, limit: int = 50
) -> list[RiskIndicatorInstance]:
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH buyer_supplier_value AS (
                    SELECT buyer_ap.entity_id AS buyer_id, ap.entity_id AS supplier_id,
                           SUM(ap.amount) AS value, COUNT(DISTINCT a.id) AS contract_count
                    FROM procurement_acts a
                    JOIN act_parties ap ON ap.act_id = a.id AND ap.party_role IN ('SUPPLIER','CONTRACTOR')
                    JOIN act_parties buyer_ap ON buyer_ap.act_id = a.id AND buyer_ap.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
                    WHERE a.act_type = 'CONTRACT' AND a.is_current = TRUE
                    GROUP BY buyer_ap.entity_id, ap.entity_id
                ), buyer_totals AS (
                    SELECT buyer_id, SUM(value) AS total_value, SUM(contract_count) AS total_contracts
                    FROM buyer_supplier_value GROUP BY buyer_id
                ), top_supplier AS (
                    SELECT DISTINCT ON (buyer_id) buyer_id, supplier_id, value AS top_value
                    FROM buyer_supplier_value ORDER BY buyer_id, value DESC
                )
                SELECT bt.buyer_id, buyer.canonical_name AS buyer_name,
                       ts.supplier_id, supplier.canonical_name AS supplier_name,
                       ts.top_value / NULLIF(bt.total_value, 0) AS top_supplier_share,
                       bt.total_contracts
                FROM buyer_totals bt
                JOIN top_supplier ts ON ts.buyer_id = bt.buyer_id
                JOIN entities buyer ON buyer.id = bt.buyer_id
                JOIN entities supplier ON supplier.id = ts.supplier_id
                WHERE bt.total_contracts >= :minimum_contracts
                  AND ts.top_value / NULLIF(bt.total_value, 0) >= :benchmark
                ORDER BY top_supplier_share DESC
                LIMIT :limit
                """
            ),
            {"minimum_contracts": minimum_contracts, "benchmark": benchmark, "limit": limit},
        )
    ).mappings().all()
    calculated_at = _now()
    return [
        RiskIndicatorInstance(
            indicator_type="HIGH_BUYER_CONCENTRATION",
            message=NON_ACCUSATORY_MESSAGE,
            subject={
                "buyer_id": str(row["buyer_id"]), "buyer_name": row["buyer_name"],
                "top_supplier_id": str(row["supplier_id"]), "top_supplier_name": row["supplier_name"],
            },
            value=row["top_supplier_share"],
            benchmark=benchmark,
            minimum_sample=minimum_contracts,
            sample_size=row["total_contracts"],
            confidence=_confidence_from_sample(row["total_contracts"], high=15, medium=5),
            sources=["procurement_acts", "act_parties"],
            calculated_at=calculated_at,
            limitations=(
                "Αντανακλά μόνο συμβάσεις καταγεγραμμένες σε αυτή την πλατφόρμα· "
                "υψηλή συγκέντρωση μπορεί να αντιστοιχεί σε νόμιμη εξειδικευμένη αγορά "
                "με λίγους διαθέσιμους προμηθευτές."
            ),
            definition="top_supplier_share = αξία ανάθεσης στον κύριο προμηθευτή / συνολική αξία αναθέσεων του φορέα (§27.4)",
        )
        for row in rows
    ]


async def repeat_same_contractor(
    conn: AsyncConnection, *, benchmark: Decimal = Decimal("0.80"), minimum_contracts: int = 5, limit: int = 50
) -> list[RiskIndicatorInstance]:
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH buyer_cpv_supplier AS (
                    SELECT buyer_ap.entity_id AS buyer_id, LEFT(cpv.cpv_code, 4) AS cpv_prefix_4,
                           ap.entity_id AS supplier_id, COUNT(DISTINCT a.id) AS contract_count
                    FROM procurement_acts a
                    JOIN act_parties ap ON ap.act_id = a.id AND ap.party_role IN ('SUPPLIER','CONTRACTOR')
                    JOIN act_parties buyer_ap ON buyer_ap.act_id = a.id AND buyer_ap.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
                    JOIN act_cpv_codes cpv ON cpv.act_id = a.id AND cpv.is_primary = TRUE
                    WHERE a.act_type = 'CONTRACT' AND a.is_current = TRUE
                    GROUP BY buyer_ap.entity_id, LEFT(cpv.cpv_code, 4), ap.entity_id
                ), totals AS (
                    SELECT buyer_id, cpv_prefix_4, SUM(contract_count) AS total_contracts
                    FROM buyer_cpv_supplier GROUP BY buyer_id, cpv_prefix_4
                ), top AS (
                    SELECT DISTINCT ON (buyer_id, cpv_prefix_4) buyer_id, cpv_prefix_4, supplier_id, contract_count
                    FROM buyer_cpv_supplier ORDER BY buyer_id, cpv_prefix_4, contract_count DESC
                )
                SELECT t.buyer_id, buyer.canonical_name AS buyer_name, t.cpv_prefix_4,
                       top.supplier_id, supplier.canonical_name AS supplier_name,
                       top.contract_count, tot.total_contracts,
                       top.contract_count::numeric / NULLIF(tot.total_contracts, 0) AS repeat_share
                FROM top
                JOIN totals tot ON tot.buyer_id = top.buyer_id AND tot.cpv_prefix_4 = top.cpv_prefix_4
                JOIN totals t ON t.buyer_id = top.buyer_id AND t.cpv_prefix_4 = top.cpv_prefix_4
                JOIN entities buyer ON buyer.id = top.buyer_id
                JOIN entities supplier ON supplier.id = top.supplier_id
                WHERE tot.total_contracts >= :minimum_contracts
                  AND top.contract_count::numeric / NULLIF(tot.total_contracts, 0) >= :benchmark
                ORDER BY repeat_share DESC
                LIMIT :limit
                """
            ),
            {"minimum_contracts": minimum_contracts, "benchmark": benchmark, "limit": limit},
        )
    ).mappings().all()
    calculated_at = _now()
    return [
        RiskIndicatorInstance(
            indicator_type="REPEAT_SAME_CONTRACTOR",
            message=NON_ACCUSATORY_MESSAGE,
            subject={
                "buyer_id": str(row["buyer_id"]), "buyer_name": row["buyer_name"],
                "cpv_prefix_4": row["cpv_prefix_4"],
                "supplier_id": str(row["supplier_id"]), "supplier_name": row["supplier_name"],
            },
            value=row["repeat_share"],
            benchmark=benchmark,
            minimum_sample=minimum_contracts,
            sample_size=row["total_contracts"],
            confidence=_confidence_from_sample(row["total_contracts"], high=15, medium=5),
            sources=["procurement_acts", "act_parties", "act_cpv_codes"],
            calculated_at=calculated_at,
            limitations=(
                "Μέτρηση βάσει πλήθους συμβάσεων, όχι αξίας· ένας μικρός αριθμός "
                "αδειοδοτημένων/εξειδικευμένων προμηθευτών σε μια κατηγορία CPV "
                "μπορεί νόμιμα να παράγει υψηλό ποσοστό επανάληψης."
            ),
            definition="repeat_share = πλήθος συμβάσεων στον πιο συχνό προμηθευτή / σύνολο συμβάσεων φορέα+CPV",
        )
        for row in rows
    ]


async def few_distinct_suppliers(
    conn: AsyncConnection, *, max_suppliers: int = 2, minimum_contracts: int = 5, limit: int = 50
) -> list[RiskIndicatorInstance]:
    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT cpv_prefix_4, nuts_code, period_year, supplier_count, contract_count
                FROM market_value_metrics
                WHERE supplier_count <= :max_suppliers AND contract_count >= :minimum_contracts
                ORDER BY contract_count DESC
                LIMIT :limit
                """
            ),
            {"max_suppliers": max_suppliers, "minimum_contracts": minimum_contracts, "limit": limit},
        )
    ).mappings().all()
    calculated_at = _now()
    return [
        RiskIndicatorInstance(
            indicator_type="FEW_DISTINCT_SUPPLIERS",
            message=NON_ACCUSATORY_MESSAGE,
            subject={
                "cpv_prefix_4": row["cpv_prefix_4"], "nuts_code": row["nuts_code"], "period_year": row["period_year"],
            },
            value=row["supplier_count"],
            benchmark=max_suppliers,
            minimum_sample=minimum_contracts,
            sample_size=row["contract_count"],
            confidence=_confidence_from_sample(row["contract_count"], high=15, medium=5),
            sources=["market_value_metrics"],
            calculated_at=calculated_at,
            limitations=(
                "Μικρές/εξειδικευμένες αγορές μπορεί νόμιμα να έχουν λίγους "
                "διαθέσιμους προμηθευτές· δεν αποτελεί ένδειξη περιορισμού ανταγωνισμού."
            ),
            definition="supplier_count ανά (CPV-4, NUTS, έτος) από market_value_metrics (§27.1)",
        )
        for row in rows
    ]


async def repeated_modifications(
    conn: AsyncConnection, *, benchmark: int = 3, limit: int = 50
) -> list[RiskIndicatorInstance]:
    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT stats.contract_act_id, a.title, stats.amendment_count
                FROM contract_modification_stats stats
                JOIN procurement_acts a ON a.id = stats.contract_act_id
                WHERE stats.amendment_count >= :benchmark AND a.is_current = TRUE
                ORDER BY stats.amendment_count DESC
                LIMIT :limit
                """
            ),
            {"benchmark": benchmark, "limit": limit},
        )
    ).mappings().all()
    calculated_at = _now()
    return [
        RiskIndicatorInstance(
            indicator_type="REPEATED_MODIFICATIONS",
            message=NON_ACCUSATORY_MESSAGE,
            subject={"contract_act_id": str(row["contract_act_id"]), "title": row["title"]},
            value=row["amendment_count"],
            benchmark=benchmark,
            minimum_sample=1,
            sample_size=1,
            confidence="LOW",
            sources=["act_links (AMENDS)", "procurement_acts"],
            calculated_at=calculated_at,
            limitations=(
                "Ανά-σύμβαση δείκτης, χωρίς σύγκριση με ιστορικό βάσης αναφοράς· "
                "τροποποιήσεις μπορεί να αντιστοιχούν σε νόμιμες, τεκμηριωμένες ανάγκες."
            ),
            definition="amendment_count από act_links(link_type='AMENDS') ανά σύμβαση (§27.7)",
        )
        for row in rows
    ]


async def large_value_increase(
    conn: AsyncConnection, *, benchmark: Decimal = Decimal("0.50"), limit: int = 50
) -> list[RiskIndicatorInstance]:
    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT stats.contract_act_id, a.title, stats.original_value, stats.current_value, stats.value_uplift_ratio
                FROM contract_modification_stats stats
                JOIN procurement_acts a ON a.id = stats.contract_act_id
                WHERE stats.value_uplift_ratio >= :benchmark AND a.is_current = TRUE
                ORDER BY stats.value_uplift_ratio DESC
                LIMIT :limit
                """
            ),
            {"benchmark": benchmark, "limit": limit},
        )
    ).mappings().all()
    calculated_at = _now()
    return [
        RiskIndicatorInstance(
            indicator_type="LARGE_VALUE_INCREASE",
            message=NON_ACCUSATORY_MESSAGE,
            subject={
                "contract_act_id": str(row["contract_act_id"]), "title": row["title"],
                "original_value": row["original_value"], "current_value": row["current_value"],
            },
            value=row["value_uplift_ratio"],
            benchmark=benchmark,
            minimum_sample=1,
            sample_size=1,
            confidence="LOW",
            sources=["contract_modification_stats", "act_links (AMENDS)"],
            calculated_at=calculated_at,
            limitations=(
                "Ανά-σύμβαση δείκτης· αύξηση αξίας μπορεί να αντιστοιχεί σε "
                "τεκμηριωμένη επέκταση αντικειμένου, όχι απαραίτητα πρόβλημα."
            ),
            definition="value_uplift_ratio = (τρέχουσα αξία - αρχική αξία) / αρχική αξία (§27.8)",
        )
        for row in rows
    ]


async def unusual_award_to_contract_delay(
    conn: AsyncConnection, *, benchmark_multiplier: Decimal = Decimal("3.0"), minimum_sample: int = 20, limit: int = 50
) -> list[RiskIndicatorInstance]:
    baseline = (
        await conn.execute(
            sa.text(
                """
                SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY award_to_contract_days) AS median_days,
                       COUNT(*) AS sample_size
                FROM cycle_time_metrics WHERE award_to_contract_days IS NOT NULL
                """
            )
        )
    ).mappings().one()
    if baseline["sample_size"] < minimum_sample or baseline["median_days"] is None:
        return []
    median_days = Decimal(str(baseline["median_days"]))
    benchmark_days = median_days * benchmark_multiplier
    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT ctm.process_id, pp.title, ctm.award_to_contract_days
                FROM cycle_time_metrics ctm
                JOIN procurement_processes pp ON pp.id = ctm.process_id
                WHERE ctm.award_to_contract_days >= :benchmark_days
                ORDER BY ctm.award_to_contract_days DESC
                LIMIT :limit
                """
            ),
            {"benchmark_days": benchmark_days, "limit": limit},
        )
    ).mappings().all()
    calculated_at = _now()
    return [
        RiskIndicatorInstance(
            indicator_type="UNUSUAL_AWARD_TO_CONTRACT_DELAY",
            message=NON_ACCUSATORY_MESSAGE,
            subject={"process_id": str(row["process_id"]), "title": row["title"]},
            value=row["award_to_contract_days"],
            benchmark=benchmark_days,
            minimum_sample=minimum_sample,
            sample_size=baseline["sample_size"],
            confidence=_confidence_from_sample(baseline["sample_size"], high=100, medium=40),
            sources=["cycle_time_metrics", "act_links (EXECUTES)"],
            calculated_at=calculated_at,
            limitations=(
                "Το benchmark είναι η διάμεσος καθυστέρησης ανάθεσης→σύμβασης σε όλη "
                "την πλατφόρμα επί πολλαπλασιαστή, όχι εξειδικευμένο ανά κατηγορία· "
                "καθυστερήσεις μπορεί να οφείλονται σε νόμιμες διοικητικές διαδικασίες."
            ),
            definition=f"award_to_contract_days >= {benchmark_multiplier}× διάμεσος πλατφόρμας ({median_days:.1f} ημέρες) (§27.9)",
        )
        for row in rows
    ]


async def company_inactive_in_later_snapshot(
    conn: AsyncConnection, *, limit: int = 50
) -> list[RiskIndicatorInstance]:
    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT DISTINCT ap.entity_id AS supplier_id, e.canonical_name AS supplier_name,
                       snap.company_status, snap.observed_at, a.id AS contract_act_id, a.title
                FROM procurement_acts a
                JOIN act_parties ap ON ap.act_id = a.id AND ap.party_role IN ('SUPPLIER','CONTRACTOR')
                JOIN entities e ON e.id = ap.entity_id
                JOIN entity_company_snapshots snap ON snap.entity_id = ap.entity_id AND snap.is_current = TRUE
                WHERE a.act_type = 'CONTRACT' AND a.is_current = TRUE
                  AND (a.end_date IS NULL OR a.end_date >= CURRENT_DATE)
                  AND snap.company_status = ANY(:inactive_statuses)
                ORDER BY snap.observed_at DESC
                LIMIT :limit
                """
            ),
            {"inactive_statuses": list(_INACTIVE_STATUSES), "limit": limit},
        )
    ).mappings().all()
    calculated_at = _now()
    return [
        RiskIndicatorInstance(
            indicator_type="COMPANY_INACTIVE_IN_LATER_SNAPSHOT",
            message=NON_ACCUSATORY_MESSAGE,
            subject={
                "supplier_id": str(row["supplier_id"]), "supplier_name": row["supplier_name"],
                "contract_act_id": str(row["contract_act_id"]), "title": row["title"],
                "company_status": row["company_status"],
            },
            value=row["company_status"],
            benchmark="ACTIVE",
            minimum_sample=1,
            sample_size=1,
            confidence="MEDIUM",
            sources=["entity_company_snapshots (ΓΕΜΗ)", "procurement_acts"],
            calculated_at=calculated_at,
            limitations=(
                "Βασίζεται στο πιο πρόσφατο ΓΕΜΗ snapshot που έχει συλλέξει η πλατφόρμα· "
                "το snapshot μπορεί να είναι ξεπερασμένο ή η κατάσταση να έχει ήδη αλλάξει ξανά."
            ),
            definition="ενεργή σύμβαση με προμηθευτή του οποίου το τρέχον snapshot company_status != 'ACTIVE' (§18.2)",
        )
        for row in rows
    ]


async def short_submission_deadline(
    conn: AsyncConnection,
    *,
    benchmark_days: Decimal = Decimal("7"),
    limit: int = 50,
) -> list[RiskIndicatorInstance]:
    rows = (
        await conn.execute(
            sa.text(
                """
                SELECT a.id AS act_id,a.process_id,a.title,a.publication_date,
                       a.submission_deadline,
                       EXTRACT(EPOCH FROM (
                           a.submission_deadline - a.publication_date::timestamptz
                       )) / 86400.0 AS deadline_days
                FROM procurement_acts a
                WHERE a.act_type IN ('REQUEST','NOTICE','TED_NOTICE')
                  AND a.is_current=TRUE
                  AND a.publication_date IS NOT NULL
                  AND a.submission_deadline IS NOT NULL
                  AND a.submission_deadline >= a.publication_date::timestamptz
                  AND EXTRACT(EPOCH FROM (
                      a.submission_deadline - a.publication_date::timestamptz
                  )) / 86400.0 <= :benchmark_days
                ORDER BY deadline_days,a.publication_date DESC LIMIT :limit
                """
            ),
            {"benchmark_days": benchmark_days, "limit": limit},
        )
    ).mappings().all()
    calculated_at = _now()
    return [
        RiskIndicatorInstance(
            indicator_type="SHORT_SUBMISSION_DEADLINE",
            message=NON_ACCUSATORY_MESSAGE,
            subject={
                "act_id": str(row["act_id"]),
                "process_id": str(row["process_id"]) if row["process_id"] else None,
                "title": row["title"],
                "publication_date": row["publication_date"].isoformat(),
                "submission_deadline": row["submission_deadline"].isoformat(),
            },
            value=Decimal(str(row["deadline_days"])),
            benchmark=benchmark_days,
            minimum_sample=1,
            sample_size=1,
            confidence="HIGH",
            sources=["procurement_acts.publication_date", "procurement_acts.submission_deadline"],
            calculated_at=calculated_at,
            limitations=(
                "Η προθεσμία μπορεί να είναι σύννομη λόγω διαδικασίας, επείγοντος ή "
                "διορθωτικής δημοσίευσης· ο δείκτης δεν αξιολογεί τη νομική βάση."
            ),
            definition="deadline_days = submission_deadline - publication_date; flag όταν deadline_days <= benchmark",
        )
        for row in rows
    ]


async def historical_benchmark_deviation(
    conn: AsyncConnection,
    *,
    benchmark_multiplier: Decimal = Decimal("3"),
    minimum_sample: int = 8,
    limit: int = 50,
) -> list[RiskIndicatorInstance]:
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH contracts AS (
                    SELECT DISTINCT a.id,a.process_id,a.title,a.amount_net,
                           COALESCE(a.decision_date,a.publication_date,a.start_date) AS event_date,
                           buyer.entity_id AS buyer_id,LEFT(cpv.cpv_code,4) AS cpv_prefix
                    FROM procurement_acts a
                    JOIN act_parties buyer ON buyer.act_id=a.id
                        AND buyer.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
                    JOIN act_cpv_codes cpv ON cpv.act_id=a.id AND cpv.is_primary=TRUE
                    WHERE a.act_type='CONTRACT' AND a.is_current=TRUE
                      AND a.amount_net>0
                      AND COALESCE(a.decision_date,a.publication_date,a.start_date) IS NOT NULL
                )
                SELECT current.*,baseline.sample_size,baseline.median_value,
                       current.amount_net / NULLIF(baseline.median_value,0) AS deviation_ratio
                FROM contracts current
                JOIN LATERAL (
                    SELECT COUNT(*) AS sample_size,
                           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY history.amount_net) AS median_value
                    FROM contracts history
                    WHERE history.buyer_id=current.buyer_id
                      AND history.cpv_prefix=current.cpv_prefix
                      AND history.event_date<current.event_date
                      AND history.event_date>=current.event_date-INTERVAL '3 years'
                ) baseline ON baseline.sample_size>=:minimum_sample
                WHERE current.amount_net / NULLIF(baseline.median_value,0)>=:multiplier
                ORDER BY deviation_ratio DESC LIMIT :limit
                """
            ),
            {
                "minimum_sample": minimum_sample,
                "multiplier": benchmark_multiplier,
                "limit": limit,
            },
        )
    ).mappings().all()
    calculated_at = _now()
    return [
        RiskIndicatorInstance(
            indicator_type="HISTORICAL_BENCHMARK_DEVIATION",
            message=NON_ACCUSATORY_MESSAGE,
            subject={
                "act_id": str(row["id"]),
                "process_id": str(row["process_id"]) if row["process_id"] else None,
                "title": row["title"],
                "buyer_id": str(row["buyer_id"]),
                "cpv_prefix_4": row["cpv_prefix"],
                "historical_median": row["median_value"],
            },
            value=row["deviation_ratio"],
            benchmark=benchmark_multiplier,
            minimum_sample=minimum_sample,
            sample_size=row["sample_size"],
            confidence=_confidence_from_sample(row["sample_size"], high=25, medium=12),
            sources=["procurement_acts", "act_parties", "act_cpv_codes"],
            calculated_at=calculated_at,
            limitations=(
                "Η διάμεσος αφορά τον ίδιο φορέα και CPV-4 στην προηγούμενη τριετία, "
                "χωρίς προσαρμογή για ποσότητα, τεχνικό εύρος ή πληθωρισμό."
            ),
            definition="deviation_ratio = contract_value / historical median for buyer+CPV-4 over prior 3 years",
        )
        for row in rows
    ]


async def awards_near_threshold(
    conn: AsyncConnection,
    *,
    margin: Decimal = Decimal("0.02"),
    minimum_occurrences: int = 3,
    limit: int = 50,
) -> list[RiskIndicatorInstance]:
    thresholds = procurement_thresholds()
    if not thresholds:
        return []
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH contracts AS (
                    SELECT DISTINCT a.id,a.title,a.amount_net,
                           EXTRACT(YEAR FROM COALESCE(a.decision_date,a.publication_date,a.start_date))::int AS period_year,
                           buyer.entity_id AS buyer_id
                    FROM procurement_acts a
                    JOIN act_parties buyer ON buyer.act_id=a.id
                        AND buyer.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
                    WHERE a.act_type='CONTRACT' AND a.is_current=TRUE AND a.amount_net>0
                ), near AS (
                    SELECT c.*,threshold,
                           (threshold-c.amount_net)/threshold AS distance_ratio
                    FROM contracts c
                    JOIN LATERAL (
                        SELECT value AS threshold
                        FROM UNNEST(CAST(:thresholds AS numeric[])) value
                        WHERE c.amount_net<value AND c.amount_net>=value*(1-CAST(:margin AS numeric))
                        ORDER BY value LIMIT 1
                    ) selected ON TRUE
                ), repeated AS (
                    SELECT buyer_id,period_year,threshold,COUNT(*) AS occurrence_count
                    FROM near GROUP BY buyer_id,period_year,threshold
                    HAVING COUNT(*)>=:minimum_occurrences
                )
                SELECT near.*,repeated.occurrence_count,e.canonical_name AS buyer_name
                FROM near JOIN repeated USING (buyer_id,period_year,threshold)
                JOIN entities e ON e.id=near.buyer_id
                ORDER BY occurrence_count DESC,distance_ratio LIMIT :limit
                """
            ),
            {
                "thresholds": thresholds,
                "margin": margin,
                "minimum_occurrences": minimum_occurrences,
                "limit": limit,
            },
        )
    ).mappings().all()
    calculated_at = _now()
    return [
        RiskIndicatorInstance(
            indicator_type="AWARDS_NEAR_THRESHOLD",
            message=NON_ACCUSATORY_MESSAGE,
            subject={
                "act_id": str(row["id"]),
                "title": row["title"],
                "buyer_id": str(row["buyer_id"]),
                "buyer_name": row["buyer_name"],
                "period_year": row["period_year"],
                "configured_threshold": row["threshold"],
            },
            value=row["amount_net"],
            benchmark=row["threshold"],
            minimum_sample=minimum_occurrences,
            sample_size=row["occurrence_count"],
            confidence=_confidence_from_sample(row["occurrence_count"], high=8, medium=4),
            sources=["procurement_acts", "act_parties", "PROCINTEL_PROCUREMENT_THRESHOLDS_EUR"],
            calculated_at=calculated_at,
            limitations=(
                "Τα όρια είναι λειτουργική ρύθμιση screening και πρέπει να επιβεβαιώνονται "
                "για το είδος φορέα, σύμβασης, περίοδο και εξαιρέσεις της συγκεκριμένης πράξης."
            ),
            definition=f"contract value lies within {margin:.0%} below a configured threshold, repeated by buyer and year",
        )
        for row in rows
    ]


async def procurement_fragmentation(
    conn: AsyncConnection,
    *,
    window_days: int = 30,
    minimum_pieces: int = 3,
    title_similarity: Decimal = Decimal("0.65"),
    limit: int = 50,
) -> list[RiskIndicatorInstance]:
    thresholds = procurement_thresholds()
    if not thresholds:
        return []
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH contracts AS (
                    SELECT DISTINCT a.id,a.title,a.normalized_title,a.amount_net,
                           COALESCE(a.decision_date,a.publication_date,a.start_date) AS event_date,
                           buyer.entity_id AS buyer_id,LEFT(cpv.cpv_code,4) AS cpv_prefix
                    FROM procurement_acts a
                    JOIN act_parties buyer ON buyer.act_id=a.id
                        AND buyer.party_role IN ('BUYER','CONTRACTING_AUTHORITY')
                    LEFT JOIN act_cpv_codes cpv ON cpv.act_id=a.id AND cpv.is_primary=TRUE
                    WHERE a.act_type='CONTRACT' AND a.is_current=TRUE
                      AND a.amount_net>0
                      AND COALESCE(a.decision_date,a.publication_date,a.start_date) IS NOT NULL
                ), clusters AS (
                    SELECT seed.id AS seed_act_id,seed.buyer_id,seed.event_date AS window_start,
                           threshold,COUNT(DISTINCT member.id) AS piece_count,
                           SUM(member.amount_net) AS aggregate_value,
                           ARRAY_AGG(DISTINCT member.id) AS act_ids,
                           MAX(similarity(COALESCE(seed.normalized_title,seed.title,''),
                                          COALESCE(member.normalized_title,member.title,''))) AS max_title_similarity
                    FROM contracts seed
                    CROSS JOIN UNNEST(CAST(:thresholds AS numeric[])) threshold
                    JOIN contracts member ON member.buyer_id=seed.buyer_id
                        AND member.event_date BETWEEN seed.event_date
                            AND seed.event_date + CAST(:window_days AS int) * INTERVAL '1 day'
                        AND member.amount_net<threshold
                        AND (
                            member.cpv_prefix=seed.cpv_prefix
                            OR similarity(COALESCE(seed.normalized_title,seed.title,''),
                                          COALESCE(member.normalized_title,member.title,''))>=:title_similarity
                        )
                    WHERE seed.amount_net<threshold
                    GROUP BY seed.id,seed.buyer_id,seed.event_date,threshold
                    HAVING COUNT(DISTINCT member.id)>=:minimum_pieces
                       AND SUM(member.amount_net)>=threshold
                )
                SELECT DISTINCT ON (buyer_id,act_ids) clusters.*,e.canonical_name AS buyer_name
                FROM clusters JOIN entities e ON e.id=clusters.buyer_id
                ORDER BY buyer_id,act_ids,threshold,aggregate_value DESC LIMIT :limit
                """
            ),
            {
                "thresholds": thresholds,
                "window_days": window_days,
                "title_similarity": title_similarity,
                "minimum_pieces": minimum_pieces,
                "limit": limit,
            },
        )
    ).mappings().all()
    calculated_at = _now()
    return [
        RiskIndicatorInstance(
            indicator_type="PROCUREMENT_FRAGMENTATION",
            message=NON_ACCUSATORY_MESSAGE,
            subject={
                "buyer_id": str(row["buyer_id"]),
                "buyer_name": row["buyer_name"],
                "act_ids": [str(value) for value in row["act_ids"]],
                "window_start": row["window_start"].isoformat(),
                "window_days": window_days,
            },
            value=row["aggregate_value"],
            benchmark=row["threshold"],
            minimum_sample=minimum_pieces,
            sample_size=row["piece_count"],
            confidence=_confidence_from_sample(row["piece_count"], high=8, medium=4),
            sources=["procurement_acts", "act_parties", "act_cpv_codes", "pg_trgm"],
            calculated_at=calculated_at,
            limitations=(
                "Η ομαδοποίηση χρησιμοποιεί κοινό CPV-4 ή ομοιότητα τίτλου σε χρονικό "
                "παράθυρο· επαναλαμβανόμενες ανεξάρτητες ανάγκες μπορεί να είναι απολύτως νόμιμες."
            ),
            definition="at least N related below-threshold contracts by one buyer within the configured window whose aggregate crosses the threshold",
        )
        for row in rows
    ]


async def missing_expected_linked_acts(
    conn: AsyncConnection,
    *,
    ingestion_grace_days: int = 14,
    limit: int = 50,
) -> list[RiskIndicatorInstance]:
    rows = (
        await conn.execute(
            sa.text(
                """
                WITH lifecycle AS (
                    SELECT pp.id AS process_id,pp.title,
                           COUNT(*) FILTER (WHERE a.act_type IN ('REQUEST','APPROVED_REQUEST')) AS requests,
                           COUNT(*) FILTER (WHERE a.act_type IN ('NOTICE','TED_NOTICE')) AS notices,
                           COUNT(*) FILTER (WHERE a.act_type='AWARD') AS awards,
                           COUNT(*) FILTER (WHERE a.act_type='CONTRACT') AS contracts,
                           COUNT(*) FILTER (WHERE a.act_type='PAYMENT') AS payments,
                           MAX(COALESCE(a.publication_date,a.decision_date,a.submission_date,a.start_date)) AS latest_date,
                           ARRAY_AGG(DISTINCT sr.source_system) AS source_systems
                    FROM procurement_processes pp
                    JOIN procurement_acts a ON a.process_id=pp.id AND a.is_current=TRUE
                    JOIN source_records sr ON sr.id=a.source_record_id
                    WHERE pp.record_status='ACTIVE'
                    GROUP BY pp.id,pp.title
                )
                SELECT *,
                       (CASE WHEN contracts>0 AND awards=0 THEN 1 ELSE 0 END
                        + CASE WHEN contracts>0 AND notices=0 AND requests=0 THEN 1 ELSE 0 END
                        + CASE WHEN payments>0 AND contracts=0 THEN 1 ELSE 0 END
                        + CASE WHEN awards>0 AND notices=0 AND requests=0 THEN 1 ELSE 0 END) AS missing_count
                FROM lifecycle
                WHERE latest_date<=CURRENT_DATE-CAST(:grace_days AS int)
                  AND (
                      (contracts>0 AND awards=0)
                      OR (contracts>0 AND notices=0 AND requests=0)
                      OR (payments>0 AND contracts=0)
                      OR (awards>0 AND notices=0 AND requests=0)
                  )
                ORDER BY missing_count DESC,latest_date LIMIT :limit
                """
            ),
            {"grace_days": ingestion_grace_days, "limit": limit},
        )
    ).mappings().all()
    calculated_at = _now()
    instances: list[RiskIndicatorInstance] = []
    for row in rows:
        missing: list[str] = []
        if row["contracts"] and not row["awards"]:
            missing.append("AWARD")
        if row["contracts"] and not row["notices"] and not row["requests"]:
            missing.append("NOTICE_OR_REQUEST")
        if row["payments"] and not row["contracts"]:
            missing.append("CONTRACT")
        if row["awards"] and not row["notices"] and not row["requests"]:
            missing.append("NOTICE_OR_REQUEST_FOR_AWARD")
        instances.append(
            RiskIndicatorInstance(
                indicator_type="MISSING_EXPECTED_LINKED_ACTS",
                message=NON_ACCUSATORY_MESSAGE,
                subject={
                    "process_id": str(row["process_id"]),
                    "title": row["title"],
                    "missing_act_types": missing,
                    "latest_date": row["latest_date"].isoformat(),
                },
                value=row["missing_count"],
                benchmark=0,
                minimum_sample=1,
                sample_size=row["requests"] + row["notices"] + row["awards"] + row["contracts"] + row["payments"],
                confidence="MEDIUM" if len(row["source_systems"]) >= 2 else "LOW",
                sources=list(row["source_systems"]),
                calculated_at=calculated_at,
                limitations=(
                    "Απουσία από την πλατφόρμα μπορεί να οφείλεται σε καθυστέρηση, "
                    "μη διαθέσιμη πηγή, νόμιμη διαδικασία χωρίς το συνήθες στάδιο ή σφάλμα σύνδεσης."
                ),
                definition=f"process older than {ingestion_grace_days} days has a downstream act without one or more expected upstream lifecycle acts",
            )
        )
    return instances


async def compute_risk_indicators(conn: AsyncConnection, *, limit_per_indicator: int = 25) -> list[RiskIndicatorInstance]:
    """Runs every implemented §28 indicator and returns them combined,
    highest-confidence-first within each type's own natural ordering. Each
    query is independent — one indicator failing (e.g. a mart not yet
    refreshed) should not be allowed to hide the others, so callers that want
    partial-failure isolation should call the individual functions directly;
    this aggregator assumes a healthy database, matching every other
    read-mostly analytics endpoint in this codebase."""
    instances: list[RiskIndicatorInstance] = []
    instances += await high_buyer_concentration(conn, limit=limit_per_indicator)
    instances += await repeat_same_contractor(conn, limit=limit_per_indicator)
    instances += await few_distinct_suppliers(conn, limit=limit_per_indicator)
    instances += await repeated_modifications(conn, limit=limit_per_indicator)
    instances += await large_value_increase(conn, limit=limit_per_indicator)
    instances += await unusual_award_to_contract_delay(conn, limit=limit_per_indicator)
    instances += await company_inactive_in_later_snapshot(conn, limit=limit_per_indicator)
    instances += await short_submission_deadline(conn, limit=limit_per_indicator)
    instances += await historical_benchmark_deviation(conn, limit=limit_per_indicator)
    instances += await awards_near_threshold(conn, limit=limit_per_indicator)
    instances += await procurement_fragmentation(conn, limit=limit_per_indicator)
    instances += await missing_expected_linked_acts(conn, limit=limit_per_indicator)
    return instances
