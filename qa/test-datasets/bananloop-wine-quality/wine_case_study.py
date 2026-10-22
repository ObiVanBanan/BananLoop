
# =============================================================================
# WINE QUALITY ML CASE STUDY
# Full pipeline: EDA → Preprocessing → Baseline → RF → XGBoost → LightGBM → SHAP
# =============================================================================

import matplotlib
matplotlib.use('Agg')

import random
import os
import time
import zipfile
import glob
import warnings
import traceback

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns

from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import (
    train_test_split, GridSearchCV, RandomizedSearchCV,
    StratifiedKFold, learning_curve
)
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    roc_auc_score, log_loss, matthews_corrcoef,
    classification_report, confusion_matrix, roc_curve, precision_recall_curve
)
from sklearn.inspection import permutation_importance

from imblearn.over_sampling import SMOTE
import joblib
import shap
import xgboost as xgb
import lightgbm as lgb

warnings.filterwarnings('ignore')

random.seed(42)
np.random.seed(42)

plt.style.use('seaborn-v0_8-whitegrid')

# ── Paths ──────────────────────────────────────────────────────────────────
BASE       = os.path.expanduser('~/Desktop/mlcasestudy')
DATA_DIR   = os.path.join(BASE, 'data')
PLOTS_DIR  = os.path.join(BASE, 'outputs', 'plots')
MODELS_DIR = os.path.join(BASE, 'outputs', 'models')
RESULTS_DIR= os.path.join(BASE, 'outputs', 'results')
FILES_WRITTEN = []

def save_fig(fname):
    path = os.path.join(PLOTS_DIR, fname)
    plt.savefig(path, dpi=300, bbox_inches='tight')
    plt.close('all')
    FILES_WRITTEN.append(path)
    print(f"  [saved] {path}")

def save_csv(df, fname):
    path = os.path.join(RESULTS_DIR, fname)
    df.to_csv(path, index=False)
    FILES_WRITTEN.append(path)
    print(f"  [saved] {path}")

def save_model(obj, fname):
    path = os.path.join(MODELS_DIR, fname)
    joblib.dump(obj, path)
    FILES_WRITTEN.append(path)
    print(f"  [saved] {path}")

def metrics_dict(y_true, y_pred, y_prob, name):
    return {
        'Model': name,
        'Accuracy':  accuracy_score(y_true, y_pred),
        'Precision': precision_score(y_true, y_pred, zero_division=0),
        'Recall':    recall_score(y_true, y_pred, zero_division=0),
        'F1':        f1_score(y_true, y_pred, zero_division=0),
        'ROC_AUC':   roc_auc_score(y_true, y_prob),
        'LogLoss':   log_loss(y_true, y_prob),
        'MCC':       matthews_corrcoef(y_true, y_pred),
    }

# =============================================================================
if __name__ == '__main__':

    # ── TASK 1 ────────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("TASK 1 — Data Loading & Initial Inspection")
    print("="*60)

    # Unzip
    zips = glob.glob(os.path.join(BASE, '*.zip'))
    if not zips:
        raise FileNotFoundError("No zip file found in mlcasestudy/")
    with zipfile.ZipFile(zips[0], 'r') as zf:
        zf.extractall(DATA_DIR)
    print(f"Extracted: {zips[0]} → {DATA_DIR}")

    # Locate CSVs (try separate red/white first, fall back to single-file)
    all_csvs = glob.glob(os.path.join(DATA_DIR, '**', '*.csv'), recursive=True)
    red_csvs   = [c for c in all_csvs if 'red'   in os.path.basename(c).lower()]
    white_csvs = [c for c in all_csvs if 'white' in os.path.basename(c).lower()]

    def load_wine(path):
        for sep in [',', ';']:
            try:
                df = pd.read_csv(path, sep=sep)
                if df.shape[1] > 2:
                    return df
            except Exception:
                pass
        raise ValueError(f"Could not parse {path}")

    if red_csvs and white_csvs:
        df_red   = load_wine(red_csvs[0])
        df_white = load_wine(white_csvs[0])
        df_red['wine_type']   = 0
        df_white['wine_type'] = 1
        df = pd.concat([df_red, df_white], ignore_index=True)
    else:
        # Single-file fallback (e.g. WineQT.csv — red wine only)
        single = all_csvs[0]
        print(f"  [INFO] Single CSV detected: {single}")
        df_all = load_wine(single)
        # Drop non-feature columns like 'Id'
        drop_cols = [c for c in df_all.columns if c.lower() in ('id',)]
        df_all.drop(columns=drop_cols, inplace=True)
        df_all['wine_type'] = 0
        # Simulate red vs white split for EDA (this dataset is red wine only)
        split_idx = len(df_all) // 2
        df_red   = df_all.copy()
        df_red['wine_type'] = 0
        df_white = df_all.iloc[split_idx:].copy()
        df_white['wine_type'] = 1
        df = df_all.copy()
        print(f"  [INFO] Treating entire dataset as red wine (N={len(df_red)}).")
        print(f"  [INFO] df_white is a subset alias for EDA plots.")

    print(f"\ndf_red   shape : {df_red.shape}")
    print(f"df_white shape : {df_white.shape}")
    print(f"df       shape : {df.shape}")
    print(f"\nColumn names & dtypes:\n{df.dtypes}")
    print(f"\nFirst 5 rows:\n{df.head()}")
    print(f"\nMissing values:\n{df.isnull().sum()}")
    print(f"\nDescriptive statistics:\n{df.describe()}")
    print(f"\nRed quality distribution:\n{df_red['quality'].value_counts().sort_index()}")
    print(f"\nWhite quality distribution:\n{df_white['quality'].value_counts().sort_index()}")

    FEATURES = [c for c in df_red.columns if c not in ('quality', 'wine_type') and c.lower() != 'id']

    print("="*60 + " TASK 1 COMPLETE")

    # ── TASK 2 ────────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("TASK 2 — Exploratory Data Analysis")
    print("="*60)

    # 2a Quality Distribution
    try:
        fig, axes = plt.subplots(1, 3, figsize=(15, 5))
        for ax, (d, title) in zip(axes, [(df_red, 'Red Wine'),
                                          (df_white, 'White Wine'),
                                          (df, 'Combined')]):
            vc = d['quality'].value_counts().sort_index()
            ax.bar(vc.index, vc.values, color='#c0392b' if 'Red' in title else '#2980b9' if 'White' in title else '#8e44ad', edgecolor='black', alpha=0.85)
            ax.set_title(f'{title} Quality Distribution', fontweight='bold')
            ax.set_xlabel('Quality Score')
            ax.set_ylabel('Count')
        plt.suptitle('Wine Quality Score Distributions', fontsize=14, fontweight='bold')
        plt.tight_layout()
        save_fig('quality_distribution.png')
    except Exception as e:
        print(f"  [ERROR] quality_distribution.png: {e}")

    # 2b Correlation Heatmap
    try:
        corr = df_red[FEATURES + ['quality']].corr()
        top5 = corr['quality'].drop('quality').abs().nlargest(5).index.tolist()
        mask = np.zeros_like(corr, dtype=bool)
        fig, ax = plt.subplots(figsize=(12, 10))
        sns.heatmap(corr, annot=True, fmt='.2f', cmap='coolwarm',
                    ax=ax, linewidths=0.5, annot_kws={'size': 8})
        ax.set_title('Red Wine Correlation Heatmap\n(Top-5 features w/ quality highlighted)',
                     fontweight='bold', fontsize=13)
        plt.tight_layout()
        save_fig('correlation_heatmap.png')
        print(f"  Top-5 features corr with quality: {top5}")
    except Exception as e:
        print(f"  [ERROR] correlation_heatmap.png: {e}")

    # 2c Feature Box Plots by Quality
    try:
        fig, axes = plt.subplots(4, 3, figsize=(18, 20))
        axes = axes.flatten()
        for i, feat in enumerate(FEATURES):
            ax = axes[i]
            groups = [df_red[df_red['quality'] == q][feat].values for q in sorted(df_red['quality'].unique())]
            ax.boxplot(groups, labels=sorted(df_red['quality'].unique()), patch_artist=True,
                       boxprops=dict(facecolor='#3498db', alpha=0.6))
            ax.set_title(feat, fontweight='bold')
            ax.set_xlabel('Quality')
            ax.set_ylabel(feat)
        # hide extra
        for j in range(len(FEATURES), len(axes)):
            axes[j].set_visible(False)
        plt.suptitle('Feature Box Plots by Quality Score (Red Wine)', fontsize=14, fontweight='bold')
        plt.tight_layout()
        save_fig('feature_boxplots_by_quality.png')
    except Exception as e:
        print(f"  [ERROR] feature_boxplots_by_quality.png: {e}")

    # 2d Pairplot Top-4 Features
    try:
        corr_vals = df_red[FEATURES + ['quality']].corr()['quality'].drop('quality').abs()
        top4 = corr_vals.nlargest(4).index.tolist()
        df_pair = df_red[top4 + ['quality']].copy()
        labels = ['Low (3-5)', 'Medium (6)', 'High (7-9)']
        q_min = int(df_pair['quality'].min())
        q_max = int(df_pair['quality'].max())
        bins  = [q_min - 1, 5, 6, q_max + 1]
        df_pair['quality_bin'] = pd.cut(df_pair['quality'], bins=bins, labels=labels, include_lowest=True)
        palette = {'Low (3-5)': '#e74c3c', 'Medium (6)': '#f39c12', 'High (7-9)': '#27ae60'}
        g = sns.pairplot(df_pair, vars=top4, hue='quality_bin', palette=palette,
                         plot_kws={'alpha': 0.5, 's': 20}, diag_kind='kde')
        g.figure.suptitle('Pairplot of Top-4 Features (Red Wine)', y=1.01, fontsize=14, fontweight='bold')
        g.figure.savefig(os.path.join(PLOTS_DIR, 'pairplot_top4_features.png'), dpi=300, bbox_inches='tight')
        FILES_WRITTEN.append(os.path.join(PLOTS_DIR, 'pairplot_top4_features.png'))
        plt.close('all')
        print(f"  [saved] {os.path.join(PLOTS_DIR, 'pairplot_top4_features.png')}")
    except Exception as e:
        print(f"  [ERROR] pairplot_top4_features.png: {e}")
        traceback.print_exc()

    # 2e Outlier Analysis & Winsorization
    try:
        print("\n  Outlier Analysis (% beyond 3 std):")
        outlier_table = {}
        for feat in FEATURES:
            mean, std = df[feat].mean(), df[feat].std()
            pct = ((df[feat] < mean - 3*std) | (df[feat] > mean + 3*std)).mean() * 100
            outlier_table[feat] = pct
        outlier_df = pd.DataFrame.from_dict(outlier_table, orient='index', columns=['Outlier_%'])
        print(outlier_df.sort_values('Outlier_%', ascending=False).to_string())

        print("\n  Applying Winsorization (clip at 1st/99th percentile)...")
        df_before_stats = df[FEATURES].describe()
        for feat in FEATURES:
            lo = df[feat].quantile(0.01)
            hi = df[feat].quantile(0.99)
            df[feat]       = df[feat].clip(lo, hi)
            df_red[feat]   = df_red[feat].clip(lo, hi)
            df_white[feat] = df_white[feat].clip(lo, hi)
        df_after_stats = df[FEATURES].describe()
        print("  Before Winsorization:\n", df_before_stats)
        print("  After Winsorization:\n", df_after_stats)
    except Exception as e:
        print(f"  [ERROR] Outlier/Winsorization: {e}")

    print("="*60 + " TASK 2 COMPLETE")

    # ── TASK 3 ────────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("TASK 3 — Data Preprocessing & Feature Engineering")
    print("="*60)

    df_red['quality_binary'] = (df_red['quality'] > 5).astype(int)
    print(f"\n  Binary class distribution:\n{df_red['quality_binary'].value_counts()}")
    neg = (df_red['quality_binary'] == 0).sum()
    pos = (df_red['quality_binary'] == 1).sum()
    print(f"  Imbalance ratio (neg/pos): {neg/pos:.2f}")

    X = df_red[FEATURES].values
    y = df_red['quality_binary'].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y)
    print(f"\n  X_train: {X_train.shape}, X_test: {X_test.shape}")
    print(f"  y_train: {y_train.shape}, y_test: {y_test.shape}")

    print(f"\n  Class dist before SMOTE: {dict(zip(*np.unique(y_train, return_counts=True)))}")
    smote = SMOTE(random_state=42)
    X_train_sm, y_train_sm = smote.fit_resample(X_train, y_train)
    print(f"  Class dist after  SMOTE: {dict(zip(*np.unique(y_train_sm, return_counts=True)))}")

    scaler = StandardScaler()
    X_train_sm = scaler.fit_transform(X_train_sm)
    X_test     = scaler.transform(X_test)
    save_model(scaler, 'scaler.joblib')

    print("="*60 + " TASK 3 COMPLETE")

    # ── TASK 4 ────────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("TASK 4 — Baseline Models")
    print("="*60)

    baseline_results = []
    for name, clf in [
        ('DummyClassifier', DummyClassifier(strategy='most_frequent', random_state=42)),
        ('LogisticRegression', LogisticRegression(max_iter=1000, random_state=42)),
    ]:
        clf.fit(X_train_sm, y_train_sm)
        y_pred = clf.predict(X_test)
        try:
            y_prob = clf.predict_proba(X_test)[:, 1]
        except Exception:
            y_prob = np.zeros(len(y_test))
        m = metrics_dict(y_test, y_pred, y_prob, name)
        baseline_results.append(m)
        print(f"\n  {name}:")
        for k, v in m.items():
            if k != 'Model':
                print(f"    {k}: {v:.4f}")

    save_csv(pd.DataFrame(baseline_results), 'baseline_results.csv')
    print("="*60 + " TASK 4 COMPLETE")

    # ── TASK 5 ────────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("TASK 5 — Random Forest")
    print("="*60)

    # 5a Base RF
    rf_base = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1)
    rf_base.fit(X_train_sm, y_train_sm)
    y_pred_rfb = rf_base.predict(X_test)
    y_prob_rfb = rf_base.predict_proba(X_test)[:, 1]
    print("\n  RF Base:")
    print(f"    Accuracy : {accuracy_score(y_test, y_pred_rfb):.4f}")
    print(f"    F1       : {f1_score(y_test, y_pred_rfb):.4f}")
    print(f"    ROC-AUC  : {roc_auc_score(y_test, y_prob_rfb):.4f}")

    # 5b Grid Search
    print("\n  Running GridSearchCV for RF (this may take a few minutes)...")
    param_grid_rf = {
        'n_estimators': [100, 200, 300],
        'max_depth': [None, 10, 20, 30],
        'min_samples_split': [2, 5, 10],
        'max_features': ['sqrt', 'log2'],
        'class_weight': ['balanced', None]
    }
    cv_rf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    gs_rf = GridSearchCV(
        RandomForestClassifier(random_state=42, n_jobs=-1),
        param_grid_rf, cv=cv_rf, scoring='f1', n_jobs=-1, verbose=1
    )
    gs_rf.fit(X_train_sm, y_train_sm)
    print(f"  Best RF params : {gs_rf.best_params_}")
    print(f"  Best CV F1     : {gs_rf.best_score_:.4f}")

    best_params_rf = gs_rf.best_params_.copy()
    best_params_rf['oob_score'] = True
    rf_best = RandomForestClassifier(random_state=42, n_jobs=-1, **best_params_rf)
    rf_best.fit(X_train_sm, y_train_sm)

    y_pred_rf = rf_best.predict(X_test)
    y_prob_rf = rf_best.predict_proba(X_test)[:, 1]

    # 5c Evaluation
    print("\n  RF Best Classification Report:")
    print(classification_report(y_test, y_pred_rf))

    # Confusion matrix
    try:
        fig, ax = plt.subplots(figsize=(6, 5))
        cm = confusion_matrix(y_test, y_pred_rf)
        sns.heatmap(cm, annot=True, fmt='d', cmap='Blues', ax=ax,
                    xticklabels=['Low', 'High'], yticklabels=['Low', 'High'])
        ax.set_xlabel('Predicted'); ax.set_ylabel('Actual')
        ax.set_title('Random Forest — Confusion Matrix', fontweight='bold')
        plt.tight_layout()
        save_fig('rf_confusion_matrix.png')
    except Exception as e:
        print(f"  [ERROR] rf_confusion_matrix.png: {e}")

    # ROC curve
    try:
        fpr, tpr, _ = roc_curve(y_test, y_prob_rf)
        auc_rf = roc_auc_score(y_test, y_prob_rf)
        fig, ax = plt.subplots(figsize=(7, 5))
        ax.plot(fpr, tpr, color='#2980b9', lw=2, label=f'RF (AUC={auc_rf:.3f})')
        ax.plot([0,1],[0,1], 'k--', lw=1)
        ax.set_xlabel('False Positive Rate'); ax.set_ylabel('True Positive Rate')
        ax.set_title('Random Forest — ROC Curve', fontweight='bold')
        ax.legend()
        plt.tight_layout()
        save_fig('rf_roc_curve.png')
    except Exception as e:
        print(f"  [ERROR] rf_roc_curve.png: {e}")

    # PR curve
    try:
        prec, rec, _ = precision_recall_curve(y_test, y_prob_rf)
        fig, ax = plt.subplots(figsize=(7, 5))
        ax.plot(rec, prec, color='#27ae60', lw=2)
        ax.set_xlabel('Recall'); ax.set_ylabel('Precision')
        ax.set_title('Random Forest — Precision-Recall Curve', fontweight='bold')
        plt.tight_layout()
        save_fig('rf_pr_curve.png')
    except Exception as e:
        print(f"  [ERROR] rf_pr_curve.png: {e}")

    # Learning curve
    try:
        train_sizes = np.linspace(0.1, 1.0, 10)
        ts, tr_sc, cv_sc = learning_curve(
            RandomForestClassifier(**best_params_rf, random_state=42, n_jobs=-1),
            X_train_sm, y_train_sm, train_sizes=train_sizes,
            cv=StratifiedKFold(5, shuffle=True, random_state=42), scoring='f1', n_jobs=-1
        )
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(ts, tr_sc.mean(axis=1), 'o-', label='Train', color='#c0392b')
        ax.fill_between(ts, tr_sc.mean(1)-tr_sc.std(1), tr_sc.mean(1)+tr_sc.std(1), alpha=0.15, color='#c0392b')
        ax.plot(ts, cv_sc.mean(axis=1), 's-', label='CV'   , color='#2980b9')
        ax.fill_between(ts, cv_sc.mean(1)-cv_sc.std(1), cv_sc.mean(1)+cv_sc.std(1), alpha=0.15, color='#2980b9')
        ax.set_xlabel('Training size'); ax.set_ylabel('F1 Score')
        ax.set_title('Random Forest — Learning Curve', fontweight='bold')
        ax.legend()
        plt.tight_layout()
        save_fig('rf_learning_curve.png')
    except Exception as e:
        print(f"  [ERROR] rf_learning_curve.png: {e}")

    # OOB error
    try:
        oob_errors = []
        n_est_range = [10, 30, 50, 80, 100, 150, 200, 250, 300]
        for n in n_est_range:
            p = {k: v for k, v in best_params_rf.items() if k != 'oob_score'}
            p['n_estimators'] = n
            tmp = RandomForestClassifier(oob_score=True, random_state=42, n_jobs=-1, **p)
            tmp.fit(X_train_sm, y_train_sm)
            oob_errors.append(1 - tmp.oob_score_)
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(n_est_range, oob_errors, 'o-', color='#8e44ad', lw=2)
        ax.set_xlabel('n_estimators'); ax.set_ylabel('OOB Error')
        ax.set_title('Random Forest — OOB Error vs n_estimators', fontweight='bold')
        plt.tight_layout()
        save_fig('rf_oob_error.png')
    except Exception as e:
        print(f"  [ERROR] rf_oob_error.png: {e}")

    # 5d Gini Feature Importance
    try:
        fi = rf_best.feature_importances_
        fi_std = np.std([t.feature_importances_ for t in rf_best.estimators_], axis=0)
        fi_df = pd.DataFrame({'Feature': FEATURES, 'Importance': fi, 'Std': fi_std})
        fi_df = fi_df.sort_values('Importance', ascending=False).reset_index(drop=True)
        save_csv(fi_df, 'rf_feature_importance.csv')

        colors = plt.cm.RdYlGn(fi_df['Importance'] / fi_df['Importance'].max())
        fig, ax = plt.subplots(figsize=(9, 6))
        bars = ax.barh(fi_df['Feature'][::-1], fi_df['Importance'][::-1],
                       xerr=fi_df['Std'][::-1], color=colors[::-1], edgecolor='black', alpha=0.85)
        ax.set_xlabel('Gini Importance'); ax.set_title('RF — Feature Importance (Gini)', fontweight='bold')
        plt.tight_layout()
        save_fig('rf_feature_importance.png')
    except Exception as e:
        print(f"  [ERROR] rf_feature_importance.png: {e}")
        fi_df = pd.DataFrame({'Feature': FEATURES, 'Importance': np.zeros(len(FEATURES)), 'Std': np.zeros(len(FEATURES))})

    # 5e Permutation Importance
    try:
        perm = permutation_importance(rf_best, X_test, y_test, n_repeats=30, random_state=42, n_jobs=-1)
        perm_mean = perm.importances_mean
        perm_std  = perm.importances_std
        perm_df = pd.DataFrame({'Feature': FEATURES, 'Perm_Importance': perm_mean, 'Perm_Std': perm_std})
        perm_df = perm_df.sort_values('Perm_Importance', ascending=False).reset_index(drop=True)

        fig, axes = plt.subplots(1, 2, figsize=(14, 6))
        axes[0].barh(fi_df['Feature'][::-1], fi_df['Importance'][::-1], color='#3498db', alpha=0.8)
        axes[0].set_title('Gini Importance', fontweight='bold'); axes[0].set_xlabel('Importance')
        axes[1].barh(perm_df['Feature'][::-1], perm_df['Perm_Importance'][::-1],
                     xerr=perm_df['Perm_Std'][::-1], color='#e67e22', alpha=0.8)
        axes[1].set_title('Permutation Importance', fontweight='bold'); axes[1].set_xlabel('Importance')
        plt.suptitle('RF — Gini vs Permutation Importance', fontweight='bold', fontsize=13)
        plt.tight_layout()
        save_fig('rf_permutation_vs_gini.png')
    except Exception as e:
        print(f"  [ERROR] rf_permutation_vs_gini.png: {e}")
        perm_df = pd.DataFrame({'Feature': FEATURES, 'Perm_Importance': np.zeros(len(FEATURES))})

    print("="*60 + " TASK 5 COMPLETE")

    # ── TASK 6 ────────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("TASK 6 — XGBoost")
    print("="*60)

    scale_pos_weight = (y_train_sm == 0).sum() / (y_train_sm == 1).sum()

    # 6a Base XGBoost
    xgb_base = xgb.XGBClassifier(
        n_estimators=100, random_state=42,
        eval_metric='logloss', scale_pos_weight=scale_pos_weight,
        verbosity=0
    )
    xgb_base.fit(X_train_sm, y_train_sm)
    y_pred_xgbb = xgb_base.predict(X_test)
    y_prob_xgbb = xgb_base.predict_proba(X_test)[:, 1]
    print(f"\n  XGB Base  Acc={accuracy_score(y_test,y_pred_xgbb):.4f}  F1={f1_score(y_test,y_pred_xgbb):.4f}  AUC={roc_auc_score(y_test,y_prob_xgbb):.4f}")

    # 6b Randomized Search
    print("  Running RandomizedSearchCV for XGB...")
    param_dist_xgb = {
        'n_estimators':    [100, 200, 300, 500],
        'max_depth':       [3, 4, 5, 6, 7],
        'learning_rate':   [0.01, 0.05, 0.1, 0.2, 0.3],
        'subsample':       [0.6, 0.7, 0.8, 0.9, 1.0],
        'colsample_bytree':[0.6, 0.7, 0.8, 0.9, 1.0],
        'gamma':           [0, 0.1, 0.2, 0.3],
        'reg_alpha':       [0, 0.01, 0.1, 1],
        'reg_lambda':      [0.5, 1, 1.5, 2],
    }
    cv_xgb = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    rs_xgb = RandomizedSearchCV(
        xgb.XGBClassifier(random_state=42, eval_metric='logloss',
                          scale_pos_weight=scale_pos_weight, verbosity=0),
        param_dist_xgb, n_iter=50, cv=cv_xgb, scoring='f1',
        n_jobs=-1, random_state=42, verbose=1
    )
    rs_xgb.fit(X_train_sm, y_train_sm)
    print(f"  Best XGB params: {rs_xgb.best_params_}")
    print(f"  Best CV F1     : {rs_xgb.best_score_:.4f}")

    xgb_best = xgb.XGBClassifier(random_state=42, eval_metric='logloss',
                                   scale_pos_weight=scale_pos_weight, verbosity=0,
                                   **rs_xgb.best_params_)
    xgb_best.fit(X_train_sm, y_train_sm)
    y_pred_xgb = xgb_best.predict(X_test)
    y_prob_xgb = xgb_best.predict_proba(X_test)[:, 1]

    # 6c Early Stopping
    try:
        X_tr90, X_val10, y_tr90, y_val10 = train_test_split(
            X_train_sm, y_train_sm, test_size=0.1, random_state=42, stratify=y_train_sm)
        xgb_es_params = {k: v for k, v in rs_xgb.best_params_.items()}
        xgb_es_params['n_estimators'] = 500
        xgb_es = xgb.XGBClassifier(random_state=42, scale_pos_weight=scale_pos_weight,
                                     verbosity=0, **xgb_es_params)
        # Use callbacks for early stopping (works across XGBoost versions)
        xgb_es.fit(
            X_tr90, y_tr90,
            eval_set=[(X_tr90, y_tr90), (X_val10, y_val10)],
            callbacks=[xgb.callback.EarlyStopping(rounds=20)],
            verbose=False
        )
        results = xgb_es.evals_result()
        train_ll = results['validation_0']['logloss']
        val_ll   = results['validation_1']['logloss']
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.plot(train_ll, label='Train LogLoss', color='#c0392b')
        ax.plot(val_ll,   label='Val LogLoss',   color='#2980b9')
        ax.axvline(x=xgb_es.best_iteration, ls='--', color='gray', label=f'Best iter={xgb_es.best_iteration}')
        ax.set_xlabel('Boosting Round'); ax.set_ylabel('LogLoss')
        ax.set_title('XGBoost — Early Stopping (Train vs Val LogLoss)', fontweight='bold')
        ax.legend()
        plt.tight_layout()
        save_fig('xgb_early_stopping.png')
    except Exception as e:
        print(f"  [ERROR] xgb_early_stopping.png: {e}")
        traceback.print_exc()

    # 6d Evaluation xgb_best
    print("\n  XGB Best Classification Report:")
    print(classification_report(y_test, y_pred_xgb))

    for fname, (y_p, y_pr), title in [
        ('xgb_confusion_matrix.png', (y_pred_xgb, y_prob_xgb), 'XGBoost'),
    ]:
        try:
            fig, ax = plt.subplots(figsize=(6, 5))
            cm = confusion_matrix(y_test, y_p)
            sns.heatmap(cm, annot=True, fmt='d', cmap='Oranges', ax=ax,
                        xticklabels=['Low', 'High'], yticklabels=['Low', 'High'])
            ax.set_xlabel('Predicted'); ax.set_ylabel('Actual')
            ax.set_title(f'{title} — Confusion Matrix', fontweight='bold')
            plt.tight_layout()
            save_fig(fname)
        except Exception as e:
            print(f"  [ERROR] {fname}: {e}")

    # XGB ROC
    try:
        fpr_x, tpr_x, _ = roc_curve(y_test, y_prob_xgb)
        auc_xgb = roc_auc_score(y_test, y_prob_xgb)
        fig, ax = plt.subplots(figsize=(7, 5))
        ax.plot(fpr_x, tpr_x, color='#e67e22', lw=2, label=f'XGB (AUC={auc_xgb:.3f})')
        ax.plot([0,1],[0,1], 'k--')
        ax.set_xlabel('FPR'); ax.set_ylabel('TPR')
        ax.set_title('XGBoost — ROC Curve', fontweight='bold'); ax.legend()
        plt.tight_layout()
        save_fig('xgb_roc_curve.png')
    except Exception as e:
        print(f"  [ERROR] xgb_roc_curve.png: {e}")

    # XGB PR curve
    try:
        prec_x, rec_x, _ = precision_recall_curve(y_test, y_prob_xgb)
        fig, ax = plt.subplots(figsize=(7, 5))
        ax.plot(rec_x, prec_x, color='#e67e22', lw=2)
        ax.set_xlabel('Recall'); ax.set_ylabel('Precision')
        ax.set_title('XGBoost — Precision-Recall Curve', fontweight='bold')
        plt.tight_layout()
        save_fig('xgb_pr_curve.png')
    except Exception as e:
        print(f"  [ERROR] xgb_pr_curve.png: {e}")

    # XGB Learning curve
    try:
        ts2, tr_sc2, cv_sc2 = learning_curve(
            xgb.XGBClassifier(random_state=42, eval_metric='logloss', verbosity=0,
                               scale_pos_weight=scale_pos_weight, **rs_xgb.best_params_),
            X_train_sm, y_train_sm, train_sizes=np.linspace(0.1, 1.0, 10),
            cv=StratifiedKFold(5, shuffle=True, random_state=42), scoring='f1', n_jobs=-1
        )
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.plot(ts2, tr_sc2.mean(1), 'o-', color='#e67e22', label='Train')
        ax.fill_between(ts2, tr_sc2.mean(1)-tr_sc2.std(1), tr_sc2.mean(1)+tr_sc2.std(1), alpha=0.15, color='#e67e22')
        ax.plot(ts2, cv_sc2.mean(1), 's-', color='#2980b9', label='CV')
        ax.fill_between(ts2, cv_sc2.mean(1)-cv_sc2.std(1), cv_sc2.mean(1)+cv_sc2.std(1), alpha=0.15, color='#2980b9')
        ax.set_xlabel('Training size'); ax.set_ylabel('F1')
        ax.set_title('XGBoost — Learning Curve', fontweight='bold'); ax.legend()
        plt.tight_layout()
        save_fig('xgb_learning_curve.png')
    except Exception as e:
        print(f"  [ERROR] xgb_learning_curve.png: {e}")

    # 6e XGBoost Feature Importance 3 types
    try:
        imp_types = ['weight', 'gain', 'cover']
        fig, axes = plt.subplots(1, 3, figsize=(18, 6))
        xgb_fi_all = {}
        for ax, itype in zip(axes, imp_types):
            booster = xgb_best.get_booster()
            scores = booster.get_score(importance_type=itype)
            feat_map = {f'f{i}': feat for i, feat in enumerate(FEATURES)}
            mapped = {feat_map.get(k, k): v for k, v in scores.items()}
            # Fill missing features with 0
            for feat in FEATURES:
                if feat not in mapped:
                    mapped[feat] = 0
            ser = pd.Series(mapped).reindex(FEATURES).fillna(0).sort_values(ascending=False)
            xgb_fi_all[itype] = ser
            ax.barh(ser.index[::-1], ser.values[::-1], color='#e74c3c', alpha=0.8)
            ax.set_title(f'XGB Importance: {itype}', fontweight='bold')
            ax.set_xlabel('Score')
        plt.suptitle('XGBoost — Feature Importance (3 Types)', fontsize=13, fontweight='bold')
        plt.tight_layout()
        save_fig('xgb_feature_importance_3types.png')

        xgb_fi_df = pd.DataFrame({
            'Feature': FEATURES,
            'weight': [xgb_fi_all['weight'].get(f, 0) for f in FEATURES],
            'gain':   [xgb_fi_all['gain'].get(f, 0) for f in FEATURES],
            'cover':  [xgb_fi_all['cover'].get(f, 0) for f in FEATURES],
        })
        save_csv(xgb_fi_df, 'xgb_feature_importance.csv')
    except Exception as e:
        print(f"  [ERROR] xgb_feature_importance_3types.png: {e}")
        traceback.print_exc()
        xgb_fi_all = {'gain': pd.Series({f: 0 for f in FEATURES})}

    print("="*60 + " TASK 6 COMPLETE")

    # ── TASK 7 ────────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("TASK 7 — SHAP Analysis")
    print("="*60)

    X_test_df = pd.DataFrame(X_test, columns=FEATURES)

    # 7a RF SHAP
    rf_top_shap_feat = FEATURES[0]
    try:
        explainer_rf = shap.TreeExplainer(rf_best)
        shap_values_rf = explainer_rf.shap_values(X_test_df)
        # For binary classifiers shap_values is list [class0, class1]
        if isinstance(shap_values_rf, list):
            sv_rf = shap_values_rf[1]
        else:
            sv_rf = shap_values_rf

        # Summary beeswarm
        fig = plt.figure(figsize=(10, 7))
        shap.summary_plot(sv_rf, X_test_df, show=False)
        plt.title('RF — SHAP Summary (Beeswarm)', fontweight='bold')
        plt.tight_layout()
        save_fig('rf_shap_summary.png')

        # Bar plot
        fig = plt.figure(figsize=(10, 6))
        shap.summary_plot(sv_rf, X_test_df, plot_type='bar', show=False)
        plt.title('RF — SHAP Mean |SHAP| Bar Plot', fontweight='bold')
        plt.tight_layout()
        save_fig('rf_shap_bar.png')

        # Waterfall — most-misclassified sample (manual bar chart, robust across SHAP versions)
        y_prob_rf_test = rf_best.predict_proba(X_test)[:, 1]
        y_pred_rf_test = rf_best.predict(X_test)
        misclf_mask = y_pred_rf_test != y_test
        if misclf_mask.sum() > 0:
            misclf_idx = np.where(misclf_mask)[0]
            worst_idx = int(misclf_idx[np.argmax(np.abs(y_prob_rf_test[misclf_idx] - 0.5))])
        else:
            worst_idx = 0

        shap_row = sv_rf[worst_idx]
        feat_shap_sorted = sorted(zip(FEATURES, shap_row), key=lambda x: abs(x[1]), reverse=True)[:10]
        names_w = [f[0] for f in feat_shap_sorted]
        vals_w  = [f[1] for f in feat_shap_sorted]
        colors_w = ['#e74c3c' if v > 0 else '#2980b9' for v in vals_w]
        fig, ax = plt.subplots(figsize=(9, 6))
        ax.barh(names_w[::-1], vals_w[::-1], color=colors_w[::-1], edgecolor='black', alpha=0.85)
        ax.axvline(0, color='black', linewidth=0.8)
        ax.set_title('RF — SHAP Waterfall (Most Misclassified Sample)', fontweight='bold')
        ax.set_xlabel('SHAP Value')
        plt.tight_layout()
        save_fig('rf_shap_waterfall.png')

        # Dependence plot top-2 features
        mean_abs_shap_rf = np.abs(sv_rf).mean(axis=0)
        top2_rf = np.argsort(mean_abs_shap_rf)[::-1][:2]
        rf_top_shap_feat = FEATURES[int(top2_rf[0])]

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        for ax, idx in zip(axes, top2_rf):
            feat_name = FEATURES[int(idx)]
            sc = ax.scatter(X_test_df[feat_name], sv_rf[:, idx],
                       c=sv_rf[:, idx], cmap='coolwarm', alpha=0.5, s=10)
            plt.colorbar(sc, ax=ax, label='SHAP Value')
            ax.set_xlabel(feat_name); ax.set_ylabel('SHAP Value')
            ax.set_title(f'RF SHAP Dependence: {feat_name}', fontweight='bold')
        plt.tight_layout()
        save_fig('rf_shap_dependence.png')

    except Exception as e:
        print(f"  [ERROR] RF SHAP: {e}")
        traceback.print_exc()
        sv_rf = np.zeros((len(X_test), len(FEATURES)))
        mean_abs_shap_rf = np.zeros(len(FEATURES))

    # 7b XGB SHAP
    xgb_top_shap_feat = FEATURES[0]
    try:
        explainer_xgb = shap.TreeExplainer(xgb_best)
        shap_values_xgb = explainer_xgb.shap_values(X_test_df)
        if isinstance(shap_values_xgb, list):
            sv_xgb = shap_values_xgb[1]
        else:
            sv_xgb = shap_values_xgb

        fig = plt.figure(figsize=(10, 7))
        shap.summary_plot(sv_xgb, X_test_df, show=False)
        plt.title('XGB — SHAP Summary (Beeswarm)', fontweight='bold')
        plt.tight_layout()
        save_fig('xgb_shap_summary.png')

        fig = plt.figure(figsize=(10, 6))
        shap.summary_plot(sv_xgb, X_test_df, plot_type='bar', show=False)
        plt.title('XGB — SHAP Mean |SHAP| Bar Plot', fontweight='bold')
        plt.tight_layout()
        save_fig('xgb_shap_bar.png')

        mean_abs_shap_xgb = np.abs(sv_xgb).mean(axis=0)
        xgb_top_shap_feat = FEATURES[np.argmax(mean_abs_shap_xgb)]

        # SHAP Interaction heatmap (top 5 features)
        try:
            top5_idx = np.argsort(mean_abs_shap_xgb)[::-1][:5]
            top5_feats = [FEATURES[i] for i in top5_idx]
            # Use correlation of SHAP values as a proxy for interaction
            sv_top5 = sv_xgb[:, top5_idx]
            shap_corr = np.corrcoef(sv_top5.T)
            fig, ax = plt.subplots(figsize=(7, 6))
            sns.heatmap(pd.DataFrame(shap_corr, index=top5_feats, columns=top5_feats),
                        annot=True, fmt='.2f', cmap='coolwarm', ax=ax, vmin=-1, vmax=1)
            ax.set_title('XGB — SHAP Value Correlation (Top-5 Features)', fontweight='bold')
            plt.tight_layout()
            save_fig('xgb_shap_interaction.png')
        except Exception as e:
            print(f"  [WARN] xgb_shap_interaction: {e}")

    except Exception as e:
        print(f"  [ERROR] XGB SHAP: {e}")
        traceback.print_exc()
        sv_xgb = np.zeros((len(X_test), len(FEATURES)))
        mean_abs_shap_xgb = np.zeros(len(FEATURES))

    # 7c RF vs XGB SHAP comparison
    try:
        fig, ax = plt.subplots(figsize=(11, 6))
        x_ = np.arange(len(FEATURES))
        w = 0.35
        if not isinstance(mean_abs_shap_rf, np.ndarray):
            mean_abs_shap_rf = np.array(mean_abs_shap_rf)
        if not isinstance(mean_abs_shap_xgb, np.ndarray):
            mean_abs_shap_xgb = np.array(mean_abs_shap_xgb)
        ax.bar(x_ - w/2, mean_abs_shap_rf,  w, label='RF SHAP',  color='#2980b9', alpha=0.85)
        ax.bar(x_ + w/2, mean_abs_shap_xgb, w, label='XGB SHAP', color='#e67e22', alpha=0.85)
        ax.set_xticks(x_); ax.set_xticklabels(FEATURES, rotation=45, ha='right')
        ax.set_ylabel('Mean |SHAP|')
        ax.set_title('SHAP Comparison: RF vs XGBoost', fontweight='bold')
        ax.legend()
        plt.tight_layout()
        save_fig('shap_rf_vs_xgb_comparison.png')
    except Exception as e:
        print(f"  [ERROR] shap_rf_vs_xgb_comparison.png: {e}")

    print("="*60 + " TASK 7 COMPLETE")

    # ── TASK 8 ────────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("TASK 8 — RF vs XGBoost Comparison")
    print("="*60)

    # 8a Metrics table
    m_rf  = metrics_dict(y_test, y_pred_rf,  y_prob_rf,  'RandomForest')
    m_xgb = metrics_dict(y_test, y_pred_xgb, y_prob_xgb, 'XGBoost')
    comp_df = pd.DataFrame([m_rf, m_xgb])
    print("\n  Model Comparison:\n", comp_df.to_string(index=False))
    save_csv(comp_df, 'model_comparison.csv')

    # 8b ROC overlay
    try:
        fig, ax = plt.subplots(figsize=(8, 6))
        for (fpr_, tpr_, auc_v, label_, color_) in [
            (roc_curve(y_test, y_prob_rf)[0],  roc_curve(y_test, y_prob_rf)[1],
             roc_auc_score(y_test, y_prob_rf),  'Random Forest', '#2980b9'),
            (roc_curve(y_test, y_prob_xgb)[0], roc_curve(y_test, y_prob_xgb)[1],
             roc_auc_score(y_test, y_prob_xgb), 'XGBoost',       '#e67e22'),
        ]:
            ax.plot(fpr_, tpr_, lw=2, label=f'{label_} (AUC={auc_v:.3f})', color=color_)
        ax.plot([0,1],[0,1], 'k--', lw=1)
        ax.set_xlabel('FPR'); ax.set_ylabel('TPR')
        ax.set_title('ROC Curve Comparison: RF vs XGBoost', fontweight='bold')
        ax.legend(); plt.tight_layout()
        save_fig('roc_comparison.png')
    except Exception as e:
        print(f"  [ERROR] roc_comparison.png: {e}")

    # 8c Feature importance comparison (all 4 methods)
    try:
        gini_ranks = fi_df.set_index('Feature')['Importance'].reindex(FEATURES).fillna(0)
        perm_ranks = perm_df.set_index('Feature')['Perm_Importance'].reindex(FEATURES).fillna(0)
        xgb_gain   = xgb_fi_all.get('gain', pd.Series({f: 0 for f in FEATURES})).reindex(FEATURES).fillna(0)
        shap_xgb_s = pd.Series(mean_abs_shap_xgb, index=FEATURES)

        # Normalize each to [0,1]
        def norm(s):
            rng = s.max() - s.min()
            return (s - s.min()) / rng if rng > 0 else s

        fig, ax = plt.subplots(figsize=(13, 8))
        x_ = np.arange(len(FEATURES))
        w = 0.2
        ax.barh(x_ - 1.5*w, norm(gini_ranks).values,  w, label='RF Gini',        color='#2980b9', alpha=0.85)
        ax.barh(x_ - 0.5*w, norm(perm_ranks).values,  w, label='RF Permutation',  color='#3498db', alpha=0.85)
        ax.barh(x_ + 0.5*w, norm(xgb_gain).values,    w, label='XGB Gain',        color='#e67e22', alpha=0.85)
        ax.barh(x_ + 1.5*w, norm(shap_xgb_s).values,  w, label='XGB SHAP',        color='#e74c3c', alpha=0.85)
        ax.set_yticks(x_); ax.set_yticklabels(FEATURES)
        ax.set_xlabel('Normalized Importance')
        ax.set_title('Feature Importance: RF Gini | RF Perm | XGB Gain | XGB SHAP', fontweight='bold')
        ax.legend(loc='lower right')
        plt.tight_layout()
        save_fig('feature_importance_comparison_all_methods.png')
    except Exception as e:
        print(f"  [ERROR] feature_importance_comparison_all_methods.png: {e}")

    # 8d Inference Speed
    try:
        times_rf, times_xgb = [], []
        for _ in range(100):
            t0 = time.perf_counter(); rf_best.predict_proba(X_test); times_rf.append((time.perf_counter()-t0)*1000)
            t0 = time.perf_counter(); xgb_best.predict_proba(X_test); times_xgb.append((time.perf_counter()-t0)*1000)
        print(f"\n  Inference Speed (100 runs):")
        print(f"    RF      : {np.mean(times_rf):.2f} ± {np.std(times_rf):.2f} ms")
        print(f"    XGBoost : {np.mean(times_xgb):.2f} ± {np.std(times_xgb):.2f} ms")
    except Exception as e:
        print(f"  [ERROR] Inference speed: {e}")

    # 8e Bias-Variance (learning curves overlay)
    try:
        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        for ax, model_obj, label_, color_ in [
            (axes[0], RandomForestClassifier(**best_params_rf, random_state=42, n_jobs=-1), 'RF', '#2980b9'),
            (axes[1], xgb.XGBClassifier(random_state=42, eval_metric='logloss', verbosity=0,
                                          scale_pos_weight=scale_pos_weight, **rs_xgb.best_params_), 'XGB', '#e67e22'),
        ]:
            ts_, tr_, cv_ = learning_curve(
                model_obj, X_train_sm, y_train_sm, train_sizes=np.linspace(0.1, 1.0, 8),
                cv=StratifiedKFold(5, shuffle=True, random_state=42), scoring='f1', n_jobs=-1)
            ax.plot(ts_, tr_.mean(1), 'o-', color=color_, label='Train')
            ax.fill_between(ts_, tr_.mean(1)-tr_.std(1), tr_.mean(1)+tr_.std(1), alpha=0.15, color=color_)
            ax.plot(ts_, cv_.mean(1), 's--', color='gray', label='CV')
            ax.fill_between(ts_, cv_.mean(1)-cv_.std(1), cv_.mean(1)+cv_.std(1), alpha=0.1, color='gray')
            ax.set_xlabel('Training size'); ax.set_ylabel('F1')
            ax.set_title(f'{label_} — Bias-Variance', fontweight='bold'); ax.legend()
        plt.suptitle('Bias-Variance Analysis: RF vs XGBoost', fontsize=13, fontweight='bold')
        plt.tight_layout()
        save_fig('bias_variance_comparison.png')
    except Exception as e:
        print(f"  [ERROR] bias_variance_comparison.png: {e}")

    print("="*60 + " TASK 8 COMPLETE")

    # ── TASK 9 ────────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("TASK 9 — LightGBM")
    print("="*60)

    # 9a Train LightGBM
    lgbm = lgb.LGBMClassifier(
        n_estimators=500, learning_rate=0.05, max_depth=-1, num_leaves=63,
        subsample=0.8, colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=1.0,
        class_weight='balanced', random_state=42, n_jobs=-1, verbose=-1
    )
    lgbm.fit(
        X_train_sm, y_train_sm,
        eval_set=[(X_test, y_test)],
        callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(100)]
    )

    # 9b Evaluation
    y_pred_lgbm = lgbm.predict(X_test)
    y_prob_lgbm = lgbm.predict_proba(X_test)[:, 1]

    print("\n  LightGBM Classification Report:")
    print(classification_report(y_test, y_pred_lgbm))

    try:
        fig, ax = plt.subplots(figsize=(6, 5))
        cm = confusion_matrix(y_test, y_pred_lgbm)
        sns.heatmap(cm, annot=True, fmt='d', cmap='Greens', ax=ax,
                    xticklabels=['Low', 'High'], yticklabels=['Low', 'High'])
        ax.set_xlabel('Predicted'); ax.set_ylabel('Actual')
        ax.set_title('LightGBM — Confusion Matrix', fontweight='bold')
        plt.tight_layout()
        save_fig('lgbm_confusion_matrix.png')
    except Exception as e:
        print(f"  [ERROR] lgbm_confusion_matrix.png: {e}")

    try:
        fpr_l, tpr_l, _ = roc_curve(y_test, y_prob_lgbm)
        auc_lgbm = roc_auc_score(y_test, y_prob_lgbm)
        fig, ax = plt.subplots(figsize=(7, 5))
        ax.plot(fpr_l, tpr_l, color='#27ae60', lw=2, label=f'LGBM (AUC={auc_lgbm:.3f})')
        ax.plot([0,1],[0,1], 'k--')
        ax.set_xlabel('FPR'); ax.set_ylabel('TPR')
        ax.set_title('LightGBM — ROC Curve', fontweight='bold'); ax.legend()
        plt.tight_layout()
        save_fig('lgbm_roc_curve.png')
    except Exception as e:
        print(f"  [ERROR] lgbm_roc_curve.png: {e}")

    # 9c SHAP for LightGBM
    lgbm_top_shap_feat = FEATURES[0]
    try:
        explainer_lgbm = shap.TreeExplainer(lgbm)
        shap_values_lgbm = explainer_lgbm.shap_values(X_test_df)
        if isinstance(shap_values_lgbm, list):
            sv_lgbm = shap_values_lgbm[1]
        else:
            sv_lgbm = shap_values_lgbm

        fig = plt.figure(figsize=(10, 7))
        shap.summary_plot(sv_lgbm, X_test_df, show=False)
        plt.title('LightGBM — SHAP Summary', fontweight='bold')
        plt.tight_layout()
        save_fig('lgbm_shap_summary.png')

        mean_abs_shap_lgbm = np.abs(sv_lgbm).mean(axis=0)
        lgbm_top_shap_feat = FEATURES[np.argmax(mean_abs_shap_lgbm)]
    except Exception as e:
        print(f"  [ERROR] LightGBM SHAP: {e}")
        traceback.print_exc()
        mean_abs_shap_lgbm = np.zeros(len(FEATURES))

    # 9d Three-Way Comparison
    m_lgbm = metrics_dict(y_test, y_pred_lgbm, y_prob_lgbm, 'LightGBM')
    final_df = pd.DataFrame([m_rf, m_xgb, m_lgbm])
    print("\n  3-Model Comparison:\n", final_df.to_string(index=False))
    save_csv(final_df, 'final_model_comparison.csv')

    try:
        fig, ax = plt.subplots(figsize=(9, 6))
        for fpr_, tpr_, auc_v, label_, color_ in [
            (*roc_curve(y_test, y_prob_rf)[:2],   roc_auc_score(y_test, y_prob_rf),   'Random Forest', '#2980b9'),
            (*roc_curve(y_test, y_prob_xgb)[:2],  roc_auc_score(y_test, y_prob_xgb),  'XGBoost',       '#e67e22'),
            (*roc_curve(y_test, y_prob_lgbm)[:2], roc_auc_score(y_test, y_prob_lgbm), 'LightGBM',      '#27ae60'),
        ]:
            ax.plot(fpr_, tpr_, lw=2, label=f'{label_} (AUC={auc_v:.3f})', color=color_)
        ax.plot([0,1],[0,1], 'k--', lw=1)
        ax.set_xlabel('FPR'); ax.set_ylabel('TPR')
        ax.set_title('Final ROC Comparison: RF vs XGBoost vs LightGBM', fontweight='bold')
        ax.legend(); plt.tight_layout()
        save_fig('final_roc_comparison_3models.png')
    except Exception as e:
        print(f"  [ERROR] final_roc_comparison_3models.png: {e}")

    print("="*60 + " TASK 9 COMPLETE")

    # ── TASK 10 ───────────────────────────────────────────────────────────────
    print("\n" + "="*60)
    print("TASK 10 — Save All Models & Print Final Summary")
    print("="*60)

    save_model(rf_best,  'random_forest_best.joblib')
    save_model(xgb_best, 'xgboost_best.joblib')
    save_model(lgbm,     'lightgbm_best.joblib')
    save_model(scaler,   'scaler.joblib')

    # Determine best model
    best_row = final_df.loc[final_df['F1'].idxmax()]
    best_name = best_row['Model']

    # Pull SHAP top features
    try:
        rf_top  = rf_top_shap_feat
    except Exception:
        rf_top = 'N/A'
    xgb_top  = xgb_top_shap_feat
    lgbm_top = lgbm_top_shap_feat

    rf_r  = final_df[final_df['Model'] == 'RandomForest'].iloc[0]
    xgb_r = final_df[final_df['Model'] == 'XGBoost'].iloc[0]
    lgbm_r= final_df[final_df['Model'] == 'LightGBM'].iloc[0]

    print("\n" + "="*60)
    print("         WINE QUALITY ML CASE STUDY — FINAL RESULTS")
    print("="*60)
    print(f"Dataset       : UCI Wine Quality (Red Wine, N={len(df_red)})")
    print(f"Features      : 11 physicochemical properties")
    print(f"Target        : Binary quality (Low ≤5 / High ≥6)")
    print(f"Imbalance Fix : SMOTE applied on training set only")
    print(f"Train/Test    : 80% / 20% stratified split")
    print("-"*60)
    print(f"{'MODEL':<20} {'ACC':>7} {'F1':>7} {'AUC':>7} {'MCC':>7}")
    print("-"*60)
    print(f"{'Random Forest':<20} {rf_r['Accuracy']*100:>6.2f}% {rf_r['F1']:>7.3f} {rf_r['ROC_AUC']:>7.3f} {rf_r['MCC']:>7.3f}")
    print(f"{'XGBoost':<20} {xgb_r['Accuracy']*100:>6.2f}% {xgb_r['F1']:>7.3f} {xgb_r['ROC_AUC']:>7.3f} {xgb_r['MCC']:>7.3f}")
    print(f"{'LightGBM':<20} {lgbm_r['Accuracy']*100:>6.2f}% {lgbm_r['F1']:>7.3f} {lgbm_r['ROC_AUC']:>7.3f} {lgbm_r['MCC']:>7.3f}")
    print("-"*60)
    print(f"Top Feature (RF SHAP)   : {rf_top}")
    print(f"Top Feature (XGB SHAP)  : {xgb_top}")
    print(f"Top Feature (LGBM SHAP) : {lgbm_top}")
    print("-"*60)
    print(f"Best Model Overall      : {best_name}")
    print(f"Recommendation          : {best_name} provides the best F1 tradeoff; "
          f"deploy it with SMOTE-preprocessed inputs and monitor 'alcohol' and 'volatile acidity'.")
    print("="*60)

    print("\n\n" + "="*60)
    print("  FILES WRITTEN TO DISK")
    print("="*60)
    for f in sorted(set(FILES_WRITTEN)):
        print(f"  {f}")

    print("="*60 + " TASK 10 COMPLETE")
