"""Starter policy for the external CovType public proxy diagnostic."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier


class Policy:
    def __init__(self, data_dir: str):
        root = Path(data_dir)
        self.features = np.load(root / "train_features.npy", allow_pickle=False)
        self.meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
        self.query_order = np.random.default_rng(73).permutation(len(self.features))
        self.feature_order = self._choose_features()
        self.labeled: dict[int, int] = {}
        self.models: dict[tuple[int, ...], RandomForestClassifier] = {}

    def _choose_features(self) -> list[int]:
        costs = np.asarray(self.meta["costs"], dtype=np.float64)
        affordable = np.flatnonzero(costs <= float(self.meta["feature_cost"]))
        variances = np.var(self.features[:, affordable], axis=0)
        ranked = sorted(
            zip(affordable.tolist(), variances.tolist()),
            key=lambda item: (-item[1], item[0]),
        )
        return [feature_id for feature_id, _ in ranked]

    def select_queries(self, labeled, budget_left: float, prices):
        self.labeled = dict(labeled)
        remaining = [int(row_id) for row_id in self.query_order
                     if int(row_id) not in self.labeled]
        affordable = int(max(0.0, budget_left))
        if affordable <= 0:
            return []
        return remaining[:affordable]

    def select_next(self, observed, budget_left: float, prices):
        for feature_id in self.feature_order:
            if feature_id not in observed and prices[feature_id] <= budget_left:
                return int(feature_id)
        return None

    def predict(self, observed):
        if not self.labeled:
            return 0
        feature_ids = tuple(sorted(observed))
        if not feature_ids:
            labels = np.fromiter(self.labeled.values(), dtype=np.int64)
            return int(np.bincount(labels, minlength=7).argmax())
        model = self.models.get(feature_ids)
        if model is None:
            row_ids = np.fromiter(self.labeled.keys(), dtype=np.int64)
            labels = np.fromiter(self.labeled.values(), dtype=np.int64)
            model = RandomForestClassifier(
                n_estimators=80,
                max_features="sqrt",
                min_samples_leaf=2,
                class_weight="balanced_subsample",
                n_jobs=1,
                random_state=17,
            )
            model.fit(self.features[row_ids][:, feature_ids], labels)
            self.models[feature_ids] = model
        row = np.asarray([[observed[feature_id] for feature_id in feature_ids]], dtype=np.float32)
        return int(model.predict(row)[0])
