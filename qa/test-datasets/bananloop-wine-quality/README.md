# 🍷 Wine Quality Prediction — ML Case Study

> **A full academic machine learning case study comparing Random Forest, XGBoost, and LightGBM  
> for binary wine quality classification from physicochemical properties.**

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![scikit-learn](https://img.shields.io/badge/scikit--learn-1.x-orange?logo=scikit-learn&logoColor=white)](https://scikit-learn.org/)
[![XGBoost](https://img.shields.io/badge/XGBoost-2.x-red)](https://xgboost.readthedocs.io/)
[![LightGBM](https://img.shields.io/badge/LightGBM-4.x-green)](https://lightgbm.readthedocs.io/)
[![SHAP](https://img.shields.io/badge/SHAP-Explainability-purple)](https://shap.readthedocs.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Table of Contents

- [Overview](#-overview)
- [Dataset](#-dataset)
- [Project Structure](#-project-structure)
- [Installation & Setup](#-installation--setup)
- [How to Run](#-how-to-run)
- [Pipeline Overview](#-pipeline-overview)
- [Results](#-results)
- [Feature Importance](#-feature-importance)
- [Generated Outputs](#-generated-outputs)
- [Key Findings](#-key-findings)
- [Technologies Used](#-technologies-used)
- [References](#-references)

---

## 🔍 Overview

This project implements a complete, reproducible ML pipeline to predict red wine quality as **binary classification** (Low ≤ 5 / High ≥ 6) based on 11 physicochemical properties. It covers:

- ✅ Exploratory Data Analysis (EDA) with 5 plot types
- ✅ Outlier detection and Winsorization
- ✅ SMOTE for class imbalance handling
- ✅ Baseline models (Dummy + Logistic Regression)
- ✅ Random Forest with full `GridSearchCV` tuning (144 candidates × 5-fold)
- ✅ XGBoost with `RandomizedSearchCV` (50 iterations × 5-fold)
- ✅ LightGBM with early stopping
- ✅ SHAP analysis for all 3 models
- ✅ 4-method feature importance comparison
- ✅ Inference speed benchmarking
- ✅ 32 publication-quality plots · 5 result CSVs · 4 saved models

All code lives in a **single runnable script**: `wine_case_study.py`

---

## 📊 Dataset

| Property | Value |
|----------|-------|
| **Name** | WineQT — Wine Quality Dataset |
| **Source** | [Kaggle — YasserH/wine-quality-dataset](https://www.kaggle.com/datasets/yasserh/wine-quality-dataset) |
| **File** | `winequality.zip` → `WineQT.csv` (included) |
| **Samples** | 1,143 red wine entries |
| **Features** | 11 continuous physicochemical variables |
| **Target** | Binary quality (Low ≤ 5 = 0, High ≥ 6 = 1) |
| **Class split** | Low: 522 (45.7%) · High: 621 (54.3%) |

### 11 Input Features

| # | Feature | Description |
|---|---------|-------------|
| 1 | `fixed acidity` | Tartaric acid — affects stability |
| 2 | `volatile acidity` | Acetic acid — high → vinegar taste |
| 3 | `citric acid` | Adds freshness |
| 4 | `residual sugar` | Sugar remaining after fermentation |
| 5 | `chlorides` | Salt content |
| 6 | `free sulfur dioxide` | Prevents microbial growth |
| 7 | `total sulfur dioxide` | Total SO₂ (free + bound) |
| 8 | `density` | Linked to sugar/alcohol balance |
| 9 | `pH` | Acidity/alkalinity |
| 10 | `sulphates` | Antimicrobial additive |
| 11 | `alcohol` | Ethanol content (% vol) |

---

## 📁 Project Structure

```
mlcasestudy/
│
├── winequality.zip              ← Raw dataset zip
├── wine_case_study.py           ← 🔑 Single runnable script (all 10 tasks)
│
├── data/
│   └── WineQT.csv               ← Extracted dataset (auto-created on run)
│
└── outputs/
    ├── plots/                   ← 32 PNG figures (300 dpi)
    │   ├── quality_distribution.png
    │   ├── correlation_heatmap.png
    │   ├── feature_boxplots_by_quality.png
    │   ├── pairplot_top4_features.png
    │   ├── rf_confusion_matrix.png
    │   ├── rf_roc_curve.png
    │   ├── rf_pr_curve.png
    │   ├── rf_learning_curve.png
    │   ├── rf_oob_error.png
    │   ├── rf_feature_importance.png
    │   ├── rf_permutation_vs_gini.png
    │   ├── rf_shap_summary.png
    │   ├── rf_shap_bar.png
    │   ├── rf_shap_waterfall.png
    │   ├── rf_shap_dependence.png
    │   ├── xgb_confusion_matrix.png
    │   ├── xgb_roc_curve.png
    │   ├── xgb_pr_curve.png
    │   ├── xgb_learning_curve.png
    │   ├── xgb_feature_importance_3types.png
    │   ├── xgb_early_stopping.png
    │   ├── xgb_shap_summary.png
    │   ├── xgb_shap_bar.png
    │   ├── xgb_shap_interaction.png
    │   ├── shap_rf_vs_xgb_comparison.png
    │   ├── roc_comparison.png
    │   ├── feature_importance_comparison_all_methods.png
    │   ├── bias_variance_comparison.png
    │   ├── lgbm_confusion_matrix.png
    │   ├── lgbm_roc_curve.png
    │   ├── lgbm_shap_summary.png
    │   └── final_roc_comparison_3models.png
    │
    ├── results/                 ← 5 CSV result files
    │   ├── baseline_results.csv
    │   ├── rf_feature_importance.csv
    │   ├── xgb_feature_importance.csv
    │   ├── model_comparison.csv
    │   └── final_model_comparison.csv
    │
    └── models/                  ← 4 saved model files
        ├── scaler.joblib
        ├── random_forest_best.joblib
        ├── xgboost_best.joblib
        └── lightgbm_best.joblib
```

---

## ⚙️ Installation & Setup

### Prerequisites

- Python 3.9+ (Anaconda recommended on macOS — avoids OpenMP issues with XGBoost)
- `pip` or `conda`

### Option A — Anaconda (Recommended on macOS)

```bash
# All packages pre-bundled with libomp support for XGBoost
/opt/anaconda3/bin/pip install lightgbm shap imbalanced-learn
```

### Option B — Standard pip + venv

```bash
cd ~/Desktop/mlcasestudy
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install pandas numpy matplotlib seaborn scikit-learn \
            xgboost lightgbm shap imbalanced-learn joblib
```

> **macOS note:** If XGBoost fails with `libomp.dylib not found`, install via Homebrew:
> ```bash
> brew install libomp
> ```
> Or use Anaconda Python which ships with OpenMP bundled.

---

## ▶️ How to Run

```bash
# Using Anaconda Python (recommended on macOS)
/opt/anaconda3/bin/python wine_case_study.py

# Using activated venv
source venv/bin/activate
python wine_case_study.py
```

The script runs **all 10 tasks end-to-end** and takes approximately **2–4 minutes** (most time is in GridSearchCV for Random Forest and RandomizedSearchCV for XGBoost, both parallelised with `n_jobs=-1`).

On completion it prints the absolute path of every file written to disk.

---

## 🔄 Pipeline Overview

```
TASK 1  → Data Loading & Inspection
TASK 2  → EDA (5 plot types + outlier analysis + Winsorization)
TASK 3  → Preprocessing (binary target, SMOTE, StandardScaler)
TASK 4  → Baseline models (Dummy + Logistic Regression)
TASK 5  → Random Forest (base → GridSearchCV → full evaluation + SHAP)
TASK 6  → XGBoost (base → RandomizedSearchCV → early stopping + full evaluation)
TASK 7  → SHAP Analysis (RF + XGBoost + comparison)
TASK 8  → RF vs XGBoost direct comparison (7 metrics + speed benchmark)
TASK 9  → LightGBM (train + evaluate + SHAP + 3-model comparison)
TASK 10 → Save all models + print final summary
```

Each task prints a clear separator:
```
============================================================ TASK N COMPLETE
```

---

## 📈 Results

### Baseline

| Model | Accuracy | F1 | ROC-AUC | MCC |
|-------|:--------:|:--:|:-------:|:---:|
| DummyClassifier | 45.85% | 0.000 | 0.500 | 0.000 |
| Logistic Regression | 77.29% | 0.780 | 0.836 | 0.550 |

### Final Model Comparison

| Model | Accuracy | Precision | Recall | F1 | ROC-AUC | Log Loss | MCC |
|-------|:--------:|:---------:|:------:|:--:|:-------:|:--------:|:---:|
| **Random Forest** | 79.48% | 79.39% | 83.87% | 0.816 | **0.867** | **0.455** | 0.586 |
| **XGBoost** | **80.79%** | 79.85% | **86.29%** | **0.830** | 0.857 | 0.510 | **0.613** |
| **LightGBM** | **80.79%** | **81.75%** | 83.06% | 0.824 | 0.862 | 0.464 | **0.613** |

> **Winner:** XGBoost (best F1 + MCC) · RF (best ROC-AUC + Log Loss) · LightGBM (best Precision + fastest)

### Tuned Hyperparameters

**Random Forest** (GridSearchCV · 144 candidates · 5-fold):
```
class_weight=balanced · max_depth=20 · max_features=sqrt
min_samples_split=2   · n_estimators=100
Best CV F1 = 0.7970
```

**XGBoost** (RandomizedSearchCV · 50 iterations · 5-fold):
```
n_estimators=500  · max_depth=5      · learning_rate=0.20
subsample=1.0     · colsample_bytree=1.0 · gamma=0.1
reg_lambda=2.0    · reg_alpha=0
Best CV F1 = 0.7931
```

---

## 🔑 Feature Importance

All four importance methods agree on the **top 3** features:

| Rank | Feature | RF Gini | XGB Gain | SHAP (both) |
|------|---------|:-------:|:--------:|:-----------:|
| 🥇 1 | **alcohol** | 0.195 | 4.441 | ✅ #1 |
| 🥈 2 | **sulphates** | 0.131 | 2.459 | ✅ #2 |
| 🥉 3 | **volatile acidity** | 0.104 | 1.749 | ✅ #3 |
| 4 | total sulfur dioxide | 0.104 | 1.525 | — |
| 5 | density | 0.083 | 1.044 | — |

> **Key insight:** Higher alcohol → High quality. Higher volatile acidity → Low quality. These align with enological domain knowledge.

---

## 🖼️ Generated Outputs

### EDA Plots

| Plot | Description |
|------|-------------|
| `quality_distribution.png` | Bar chart of quality scores (red, white, combined) |
| `correlation_heatmap.png` | Full 11-feature correlation matrix with `coolwarm` colormap |
| `feature_boxplots_by_quality.png` | 4×3 grid of box plots per quality score |
| `pairplot_top4_features.png` | Seaborn pairplot of top-4 correlated features, coloured by quality bin |

### Model Evaluation Plots

| Plot | Description |
|------|-------------|
| `rf_confusion_matrix.png` | RF confusion matrix heatmap |
| `rf_roc_curve.png` | RF ROC curve with AUC |
| `rf_pr_curve.png` | RF Precision-Recall curve |
| `rf_learning_curve.png` | RF training vs. CV F1 vs. dataset size |
| `rf_oob_error.png` | OOB error vs. n_estimators |
| `xgb_early_stopping.png` | Train vs. validation LogLoss per boosting round |

### Feature Importance & SHAP

| Plot | Description |
|------|-------------|
| `rf_feature_importance.png` | Gini importance with ±std error bars |
| `rf_permutation_vs_gini.png` | Side-by-side Gini vs. Permutation importance |
| `rf_shap_summary.png` | SHAP beeswarm summary for RF |
| `rf_shap_waterfall.png` | SHAP waterfall for the most-misclassified sample |
| `rf_shap_dependence.png` | SHAP dependence plots for top-2 features |
| `xgb_feature_importance_3types.png` | XGB importance: Weight · Gain · Cover (3 subplots) |
| `xgb_shap_interaction.png` | SHAP value correlation heatmap (top-5 features) |
| `shap_rf_vs_xgb_comparison.png` | Side-by-side mean \|SHAP\| for all 11 features |

### Comparison Plots

| Plot | Description |
|------|-------------|
| `roc_comparison.png` | RF vs. XGBoost ROC overlay |
| `feature_importance_comparison_all_methods.png` | Grouped bar: RF Gini · RF Perm · XGB Gain · XGB SHAP |
| `bias_variance_comparison.png` | Learning curves: RF vs. XGBoost |
| `final_roc_comparison_3models.png` | RF vs. XGBoost vs. LightGBM ROC overlay |

---

## 💡 Key Findings

1. **XGBoost is the best deployment choice** (F1 = 0.830, MCC = 0.613) — highest balanced classification performance.

2. **Random Forest produces the best probability estimates** (ROC-AUC = 0.867, Log Loss = 0.455) — better suited when decision threshold flexibility is needed.

3. **LightGBM matches XGBoost on accuracy/MCC** while being ~10× faster and offering first-class SHAP integration — recommended for production.

4. **Feature consensus:** `alcohol`, `sulphates`, `volatile acidity` are the top-3 features ranked identically by all four importance methods (Gini, Permutation, XGB Gain, SHAP).

5. **Gini importance overestimates `density`** (ranked 5th by RF MDI, 11th by XGB gain). Always cross-validate with permutation importance or SHAP.

---

## 🛠️ Technologies Used

| Library | Purpose | Version |
|---------|---------|--------|
| `pandas` | Data loading and manipulation | ≥ 1.5 |
| `numpy` | Numerical computing | ≥ 1.23 |
| `matplotlib` | Plot rendering (Agg backend) | ≥ 3.6 |
| `seaborn` | Statistical visualisation | ≥ 0.12 |
| `scikit-learn` | RF, preprocessing, metrics, CV | ≥ 1.1 |
| `xgboost` | Gradient boosted trees | ≥ 2.0 |
| `lightgbm` | Fast gradient boosting | ≥ 4.0 |
| `shap` | Model explainability (SHAP values) | ≥ 0.44 |
| `imbalanced-learn` | SMOTE oversampling | ≥ 0.10 |
| `joblib` | Model serialisation | ≥ 1.2 |

---

## 📚 References

0. **YasserH** (2022). *Wine Quality Dataset.* Kaggle. https://www.kaggle.com/datasets/yasserh/wine-quality-dataset
1. Cortez et al. (2009). *Modeling wine preferences by data mining from physicochemical properties.* Decision Support Systems, 47(4).
2. Breiman, L. (2001). *Random Forests.* Machine Learning, 45(1), 5–32.
3. Chen & Guestrin (2016). *XGBoost: A scalable tree boosting system.* KDD '16.
4. Ke et al. (2017). *LightGBM: A highly efficient gradient boosting decision tree.* NeurIPS.
5. Lundberg & Lee (2017). *A unified approach to interpreting model predictions.* NeurIPS.
6. Chawla et al. (2002). *SMOTE: Synthetic Minority Over-sampling Technique.* JAIR, 16.

---

## 📄 License

This project is open for academic and educational use. Dataset sourced from [Kaggle — YasserH/wine-quality-dataset](https://www.kaggle.com/datasets/yasserh/wine-quality-dataset).



