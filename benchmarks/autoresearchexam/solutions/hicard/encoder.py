import numpy as np


class Encoder:
    def __init__(self, n_categories, max_width):
        self.width = 0

    def fit(self, X, G):
        return None

    def transform(self, X, G):
        return np.empty((len(G), self.width), dtype=np.float64)
