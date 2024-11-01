import os
import copy
import pickle
import sympy
import dowhy
import functools
import itertools
import logging
import time

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from random import choice
from error_injection import MissingValueError
from sklearn.decomposition import PCA
from sklearn.model_selection import train_test_split
from sklearn.experimental import enable_iterative_imputer
from sklearn.impute import SimpleImputer, KNNImputer, IterativeImputer
from scipy.optimize import minimize as scipy_min
from scipy.spatial import ConvexHull
from scipy.optimize import minimize, Bounds, linprog
from sympy import Symbol as sb
from sympy import lambdify
from tqdm.notebook import trange, tqdm
from dowhy import CausalModel
from dowhy import causal_estimators
import dowhy.datasets
from IPython.display import display, clear_output
from sklearn.preprocessing import StandardScaler
from sklearn.datasets import load_breast_cancer

# Config dict to set the logging level
import logging.config
DEFAULT_LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'loggers': {
        '': {
            'level': 'WARN',
        },
    }
}
logging.config.dictConfig(DEFAULT_LOGGING)
# Disabling warnings output
import warnings
from sklearn.exceptions import DataConversionWarning
warnings.filterwarnings(action='ignore', category=DataConversionWarning)

def load_data(random_seed=42):
  data = load_breast_cancer()
  df = pd.DataFrame(data.data, columns=data.feature_names)
  df['target'] = data.target

  X = df.drop(columns='target')
  y = df['target']

  X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=random_seed)
  return X_train, X_test, y_train, y_test

def create_symbol(suffix=''):
    global symbol_id
    symbol_id += 1
    name = f'e{symbol_id}_{suffix}' if suffix else f'e{symbol_id}'
    return sympy.Symbol(name=name)

def inject_ranges(X, y, uncertain_attr, uncertain_num, uncertain_radius_pct=None, 
                  uncertain_radius=None, seed=42):
    global symbol_id
    symbol_id = -1
    
    X_extended = np.append(np.ones((len(X), 1)), X, axis=1)
    ss = StandardScaler()
    X_extended[:, 1:] = ss.fit_transform(X_extended[:, 1:])
    X_extended_symb = sympy.Matrix(X_extended)
    
    if not(uncertain_attr=='y'):
        uncertain_attr_idx = X.columns.to_list().index(uncertain_attr) + 1
        if not(uncertain_radius):
            uncertain_radius = uncertain_radius_pct*(np.max(X_extended[:, uncertain_attr_idx])-\
                                                     np.min(X_extended[:, uncertain_attr_idx]))
    else:
        if not(uncertain_radius):
            uncertain_radius = uncertain_radius_pct*(y_train.max()-y_train.min())[0]
    
    np.random.seed(seed)
    uncertain_indices = np.random.choice(range(len(y)), uncertain_num, replace=False)
    y_symb = sympy.Matrix(y)
    symbols_in_data = set()
    #print(uncertain_indices)
    for uncertain_idx in uncertain_indices:
        new_symb = create_symbol()
        symbols_in_data.add(new_symb)
        if uncertain_attr=='y':
            y_symb[uncertain_idx] = y_symb[uncertain_idx] + uncertain_radius*new_symb
        else:
            X_extended_symb[uncertain_idx, uncertain_attr_idx] = X_extended_symb[uncertain_idx, uncertain_attr_idx] + uncertain_radius*new_symb
    return X_extended_symb, y_symb, symbols_in_data, ss

# if interval=True, use interval arithmetic, otherwise use zonotopes
def compute_robustness_ratio_label_error(X_train, y_train, X_test, y_test, robustness_radius,
                                         uncertain_num, uncertain_radius=None, 
                                         lr=0.1, seed=42, interval=True):
    X, y, symbols_in_data, ss = inject_ranges(X=X_train, y=y_train, uncertain_attr='y', 
                                              uncertain_num=uncertain_num, uncertain_radius=uncertain_radius, 
                                              uncertain_radius_pct=None, seed=seed)
    
    assert len(X.free_symbols)==0
    # closed-form
    param = (X.T*X).inv()*X.T*y
    
    if interval:
        # make param intervals
        for d in range(len(param)):
            expr = param[d]
            if not(expr.free_symbols):
                continue
            else:
                constant_part = 0
                interval_radius = 0
                for arg in expr.args:
                    if arg.free_symbols:
                        interval_radius += abs(arg.args[0])
                    else:
                        assert constant_part == 0
                        constant_part = arg
                param[d] = constant_part + create_symbol()*interval_radius
    
    test_preds = sympy.Matrix(np.append(np.ones((len(X_test), 1)), ss.transform(X_test), axis=1))*param
    robustness_ls = []
    for pred in test_preds:
        pred_range_radius = 0
        for arg in pred.args:
            if arg.free_symbols:
                pred_range_radius += abs(arg.args[0])
        if pred_range_radius <= robustness_radius:
            robustness_ls.append(1)
        else:
            robustness_ls.append(0)
    
#     print(param)
    return np.mean(robustness_ls)

def inject_sensitive_ranges(X, y, uncertain_attr, uncertain_num, boundary_indices, uncertain_radius_pct=None, 
                  uncertain_radius=None, seed=42):
    global symbol_id
    symbol_id = -1
    
    X_extended = np.append(np.ones((len(X), 1)), X, axis=1)
    ss = StandardScaler()
    X_extended[:, 1:] = ss.fit_transform(X_extended[:, 1:])
    X_extended_symb = sympy.Matrix(X_extended)
    
    if not(uncertain_attr=='y'):
        uncertain_attr_idx = X.columns.to_list().index(uncertain_attr) + 1
        if not(uncertain_radius):
            uncertain_radius = uncertain_radius_pct*(np.max(X_extended[:, uncertain_attr_idx])-\
                                                     np.min(X_extended[:, uncertain_attr_idx]))
    else:
        if not(uncertain_radius):
            uncertain_radius = uncertain_radius_pct*(y_train.max()-y_train.min())[0]
    
    np.random.seed(seed)
    uncertain_indices = boundary_indices[:uncertain_num]
    y_symb = sympy.Matrix(y)
    symbols_in_data = set()
    #print(uncertain_indices)
    for uncertain_idx in uncertain_indices:
        new_symb = create_symbol()
        symbols_in_data.add(new_symb)
        if uncertain_attr=='y':
            y_symb[uncertain_idx] = y_symb[uncertain_idx] + uncertain_radius*new_symb
        else:
            X_extended_symb[uncertain_idx, uncertain_attr_idx] = X_extended_symb[uncertain_idx, uncertain_attr_idx] + uncertain_radius*new_symb
    return X_extended_symb, y_symb, symbols_in_data, ss

# if interval=True, use interval arithmetic, otherwise use zonotopes
def compute_robustness_ratio_sensitive_label_error(X_train, y_train, X_test, y_test, robustness_radius,
                                         uncertain_num, boundary_indices, uncertain_radius=None, 
                                         lr=0.1, seed=42, interval=True):
    X, y, symbols_in_data, ss = inject_sensitive_ranges(X=X_train, y=y_train, uncertain_attr='y', 
                                              uncertain_num=uncertain_num, boundary_indices=boundary_indices, uncertain_radius=uncertain_radius, 
                                              uncertain_radius_pct=None, seed=seed)
    
    assert len(X.free_symbols)==0
    # closed-form
    param = (X.T*X).inv()*X.T*y
    
    if interval:
        # make param intervals
        for d in range(len(param)):
            expr = param[d]
            if not(expr.free_symbols):
                continue
            else:
                constant_part = 0
                interval_radius = 0
                for arg in expr.args:
                    if arg.free_symbols:
                        interval_radius += abs(arg.args[0])
                    else:
                        assert constant_part == 0
                        constant_part = arg
                param[d] = constant_part + create_symbol()*interval_radius
    
    test_preds = sympy.Matrix(np.append(np.ones((len(X_test), 1)), ss.transform(X_test), axis=1))*param
    robustness_ls = []
    for pred in test_preds:
        pred_range_radius = 0
        for arg in pred.args:
            if arg.free_symbols:
                pred_range_radius += abs(arg.args[0])
        if pred_range_radius <= robustness_radius:
            robustness_ls.append(1)
        else:
            robustness_ls.append(0)
    
#     print(param)
    return np.mean(robustness_ls)

def find_important_patterns(X_train_orig, y_train_orig, clf, metric, sensitivity_threshold=0.05, method="update"):
    important_indices = []
    unique_values = {col: X_train_orig[col].unique() for col in X_train_orig.columns}
    patterns = []
    
    for feature, values in unique_values.items(): #per dictionary items of "column: unique features"
        for val in values: #for feature in unique features
            if X_train_orig[feature].dtype == 'object':  # Categorical: equality check
                pattern = (X_train_orig[feature] == val)
            else:  # Numerical: threshold-based check
                pattern = (X_train_orig[feature] > val)
            patterns.append((pattern, feature, val))

    unique_indices = {}        
    
    for pattern, feature, val in tqdm(patterns, desc='Evaluating pattern performance progress'):
        
        pattern_idx = pattern[pattern].index.intersection(X_train_orig.index)  # Ensures indices align with X_train_orig

        # Clone data and update based on method
        X_modified = X_train_orig.copy().reset_index(drop=True)  # Reset index for consistency
        X_modified.loc[pattern_idx, feature] = X_modified[feature].mean() #imputation change (update based gopher)

        # Compute metric sensitivity
        y_pred = clf.predict_proba(X_modified)[:, 1]
        original_metric_value = compute_metric(y_train_orig, clf.predict_proba(X_train_orig)[:, 1], X_train_orig, metric, feature)
        updated_metric_value = compute_metric(y_train_orig, y_pred, X_train_orig, metric, feature)
      
        sensitivity = abs(updated_metric_value - original_metric_value)
        
        # Sensitivity check: if change in metric exceeds threshold, mark pattern as important
        if sensitivity > sensitivity_threshold:
            for idx in pattern_idx: # for index in the indexes meeting the threshold
                if idx not in unique_indices: #index not in dict yet
                    unique_indices[idx] = sensitivity 
                elif sensitivity > unique_indices[idx]: #index in dict already
                    unique_indices[idx] = sensitivity

    # Sort based on sensitivity while retaining original order
    ordered_indices = [idx for idx, _ in sorted(unique_indices.items(), key=lambda x: x[1], reverse=True) if idx < len(X_train_orig)]
    

    return list(dict.fromkeys(ordered_indices))  # Final deduplication

# Helper function to compute the chosen metric
def compute_metric(y_train_orig, y_pred, X, metric, feature):
    # 0 = SPD, 1 = TPR parity, 2 = predictive parity
    if metric == 0:
        return compute_spd(y_pred, X, feature)
    elif metric == 1:
        return compute_tpr_parity(y_train_orig, y_pred, X, feature)
    elif metric == 2:
        return compute_predictive_parity(y_train_orig, y_pred, X, feature)
    else:
        raise ValueError("Invalid metric type provided.")

# Statistical Parity Difference (SPD)
def compute_spd(y_pred, X, sensitive_attr):
    # Compute group-wise prediction rates
    groups = X[sensitive_attr].unique()
    group_rates = {g: np.mean(y_pred[X[sensitive_attr] == g]) for g in groups}
    # Compute SPD as max absolute difference in group rates
    spd = max(group_rates.values()) - min(group_rates.values())
    return spd

# True Positive Rate Parity (Equal Opportunity)
def compute_tpr_parity(y_true, y_pred, X, sensitive_attr):
    groups = X[sensitive_attr].unique()
    tpr = {g: np.mean((y_pred[X[sensitive_attr] == g] == 1) & (y_true[X[sensitive_attr] == g] == 1)) for g in groups}
    tpr_parity = max(tpr.values()) - min(tpr.values())
    return tpr_parity

# Predictive Parity
def compute_predictive_parity(y_true, y_pred, X, sensitive_attr):
    groups = X[sensitive_attr].unique()
    ppv = {g: np.mean(y_true[(y_pred == 1) & (X[sensitive_attr] == g)]) for g in groups}
    predictive_parity = max(ppv.values()) - min(ppv.values())
    return predictive_parity
