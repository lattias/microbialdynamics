import numpy as np

from src.transformation.fhn import fhn_transformation
from src.transformation.linear import linear_transformation
from src.transformation.lorenz import lorenz_transformation

from src.distribution.dirac_delta import dirac_delta
from src.distribution.mvn import mvn
from src.distribution.dirichlet import dirichlet
from src.distribution.poisson import poisson


G_DIST_CLASSES = dict(mvn=mvn, dirichlet=dirichlet, poisson=poisson)

def generate_hidden_obs(time, Dx, Dy, x_0, f, g, inputs=None, Dv=1):
    """
    Generate hidden states and observation
    f: transition class with x_t = g.sample(x_t-1)
    g: emission class with y_t = g.sample(x_t)
    """
    X = np.zeros((time, Dx))
    Y = np.zeros((time, Dy))

    X[0] = x_0
    Y[0] = g.sample(x_0)

    if isinstance(f.transformation, fhn_transformation):
        assert Dv == 1
        if inputs is None:
            # default input is constant value
            inputs = np.ones((time, Dv))
            #inputs = np.random.rand(time, Dv) * 3

        assert inputs.shape == (time, Dv)
        for t in range(1, time):
            X[t] = f.sample(X[t-1], inputs[t-1])
            Y[t] = g.sample(X[t])
    else:
        for t in range(1, time):
            X[t] = f.sample(X[t - 1])
            Y[t] = g.sample(X[t])
    return X, Y


def generate_dataset(n_train, n_test, time,
                     model="lorenz", Dx=1, Dy=1,
                     f=None, g=None,
                     x_0_in=None, lb=-2.5, ub=2.5, inputs=None, Dv=1,
                     g_dist="mvn"):

    if inputs is None:
        inputs = [None for _ in range(n_train + n_test)]
    else:
        assert inputs.shape == (n_train+n_test, time, Dv)

    if g_dist not in G_DIST_CLASSES:
        raise ValueError("g_dist muse be one of ", list(G_DIST_CLASSES.keys()))

    if g_dist == "dirichlet":
        assert Dy >= 2, "Dirichlet distribution must have variable dim >= 2."

    if model == "fhn":
        Dx = 2

        if f is None:
            a, b, c, dt = 1.0, 0.95, 0.05, 0.15
            f_params = (a, b, c, dt)
            f_tran = fhn_transformation(f_params)
            f = dirac_delta(f_tran)

        if g is None:
            if g_dist == "mvn":
                g_params = np.array([[1.0, 0.0]])
                g_cov = 0.01 * np.eye(Dy)
                g_tran = linear_transformation(g_params)
                g = G_DIST_CLASSES[g_dist](g_tran, g_cov)
            elif g_dist == "dirichlet":
                g_params = np.random.normal(size=(Dy, Dx))
                g_tran = linear_transformation(g_params)
                g = dirichlet(g_tran)
                g = G_DIST_CLASSES[g_dist](g_tran)

    elif model == "lorenz":
        Dx = 3

        if f is None:
            sigma, rho, beta, dt = 10.0, 28.0, 8.0 / 3.0, 0.01
            f_params = (sigma, rho, beta, dt)
            f_tran = lorenz_transformation(f_params)
            f = dirac_delta(f_tran)

        if g is None:
            g_params = np.array([[1.0, 0.0, 0.0]])
            g_cov = 0.4 * np.eye(Dy)
            g_tran = linear_transformation(g_params)
            if g_dist == "mvn":
                g = G_DIST_CLASSES[g_dist](g_tran, g_cov)
    elif model is not None:
        raise ValueError("Unknown model {}".format(model))

    hidden_train, obs_train = np.zeros((n_train, time, Dx)), np.zeros((n_train, time, Dy))
    hidden_test, obs_test = np.zeros((n_test, time, Dx)), np.zeros((n_test, time, Dy))

    if x_0_in is None and (lb and ub) is None:
        assert False, 'must specify x_0 or (lb and ub)'

    for i in range(n_train + n_test):
        if x_0_in is None:
            x_0 = np.random.uniform(low=lb, high=ub, size=Dx)
            hidden, obs = generate_hidden_obs(time, Dx, Dy, x_0, f, g, inputs=inputs[i], Dv=Dv)
        else:
            hidden, obs = generate_hidden_obs(time, Dx, Dy, x_0_in, f, g, inputs=inputs[i], Dv=Dv)
        if i < n_train:
            hidden_train[i] = hidden
            obs_train[i] = obs
        else:
            hidden_test[i - n_train] = hidden
            obs_test[i - n_train] = obs

    return hidden_train, hidden_test, obs_train, obs_test
