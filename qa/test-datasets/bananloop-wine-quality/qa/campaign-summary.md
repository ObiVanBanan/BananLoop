# BananLoop WineQT campaign summary

- Campaign: `c-6482e268b37321166b5ffbaf04807e43`
- Goal: improve ROC-AUC for the WineQT binary quality classifier.
- Result: `STOPPED` with `PLATEAU`; no candidate was promoted.
- DEV champion: `0.865591` ROC-AUC, unchanged from baseline.
- Attempts: 3 scientific experiments; 6 agent calls; 6 evaluator calls.
- Final holdout ran after candidate selection; its aggregate score is omitted here.

| Experiment | Change in `wine_model.py` | DEV ROC-AUC | Decision |
|---|---|---:|---|
| 1 | Scaled RBF SVM | `0.841475` (`-0.024117`) | Rejected |
| 2 | Shrinkage Linear Discriminant Analysis | `0.822427` (`-0.043164`) | Rejected |
| 3 | Histogram gradient boosting | `0.849539` (`-0.016052`) | Rejected |

## Setup notes

The upstream project had no callable training interface, so this checkout adds
`wine_model.py` with `train_predict(X_train, y_train, X_eval)`. The upstream
checkout also included a committed 375 MB virtual environment; it was removed
from this local copy and `venv/` is now ignored so BananLoop can create clean
snapshots within the available disk space.

BananLoop needed three readiness attempts. It repaired the evaluator's
bytecode-write behavior, then passed readiness and completed the baseline,
three DEV comparisons, validation, and final-only holdout evaluation.
