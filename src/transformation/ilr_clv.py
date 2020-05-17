import tensorflow as tf

from src.transformation.base import transformation
from src.transformation.ilr_clv_utils import convert_theta_to_tree
from src.transformation.ilr_clv_utils import get_p_i, get_between_group_p_j_given_i, get_psi, get_in_group_p_j_given_i
from src.transformation.ilr_clv_utils import inverse_ilr_transform, get_inode_relative_abundance
from src.transformation.l0_utils import hard_concrete_sample, hard_concrete_mean, l0_norm

EPS = 1e-6


class ilr_clv_transformation(transformation):
    def __init__(self, theta, Dev,
                 exist_in_group_dynamics=False, training=False,
                 use_L0=True, b_dropout_rate=0.5):
        self.Dx, self.Dy = theta.shape
        self.Dev = Dev
        self.use_L0 = use_L0

        # init variable
        self.A_in = tf.Variable(tf.zeros((self.Dx, self.Dx + self.Dy)))
        self.g_in = tf.Variable(tf.zeros((self.Dx,)))
        self.Wv_in = tf.Variable(tf.zeros((self.Dx, self.Dev)))

        self.A_between = tf.Variable(tf.zeros((self.Dx, self.Dx + self.Dy)))
        self.g_between = tf.Variable(tf.zeros((self.Dx,)))
        self.Wv_between = tf.Variable(tf.zeros((self.Dx, self.Dev)))

        # init tree
        self.root, self.reference = convert_theta_to_tree(theta)
        self.psi = get_psi(theta)

        # L0 regularization for interaction coefficients
        if use_L0:
            log_alpha_init = tf.log(b_dropout_rate / (1 - b_dropout_rate))
            self.A_in_log_alpha = tf.Variable(log_alpha_init * tf.ones_like(self.A_in, dtype=tf.float32))
            self.A_between_log_alpha = tf.Variable(log_alpha_init * tf.ones_like(self.A_between, dtype=tf.float32))
            A_in_noises = tf.cond(training,
                                  lambda: hard_concrete_sample(self.A_in_log_alpha),
                                  lambda: hard_concrete_mean(self.A_in_log_alpha))
            A_between_noises = tf.cond(training,
                                       lambda: hard_concrete_sample(self.A_between_log_alpha),
                                       lambda: hard_concrete_mean(self.A_between_log_alpha))
        else:
            A_in_noises = tf.ones_like(self.A_in, dtype=tf.float32)
            A_between_noises = tf.ones_like(self.A_between, dtype=tf.float32)

        b = []
        for inode in self.reference[:self.Dx]:
            b.append(inode.b)
        self.b = tf.stack(b)

        self.p_i = get_p_i(self.Dx, self.root)
        self.in_group_p_j_given_i = get_in_group_p_j_given_i(self.Dx + self.Dy, self.Dx, self.root)
        self.between_group_p_j_given_i = get_between_group_p_j_given_i(self.Dx + self.Dy, self.Dx, self.root)

        self.A_in *= A_in_noises * self.in_group_p_j_given_i * exist_in_group_dynamics
        self.g_in *= (1 - self.p_i) * exist_in_group_dynamics
        self.Wv_in *= (1 - self.p_i[:, None]) * exist_in_group_dynamics

        self.A_between *= A_between_noises * self.p_i[:, None] * self.between_group_p_j_given_i
        self.g_between *= self.p_i
        self.Wv_between *= self.p_i[:, None]

        self.params = {"b": self.b,
                       "A_in": self.A_in, "g_in": self.g_in, "Wv_in": self.Wv_in,
                       "A_between": self.A_between, "g_between": self.g_between, "Wv_between": self.Wv_between,
                       "p_i": self.p_i,
                       "in_group_p_j_given_i": self.in_group_p_j_given_i,
                       "between_group_p_j_given_i": self.between_group_p_j_given_i}

    def regularization_loss(self):
        L2 = tf.reduce_sum(self.g_in ** 2) + tf.reduce_sum(self.Wv_in ** 2) + \
             tf.reduce_sum(self.g_between ** 2) + tf.reduce_sum(self.Wv_between ** 2)
        if self.use_L0:
            A_in_l0_norm = l0_norm(self.A_in_log_alpha)
            A_between_l0_norm = l0_norm(self.A_between_log_alpha)
            L0 = A_in_l0_norm + A_between_l0_norm
            L2 += A_in_l0_norm * tf.reduce_sum(self.A_in ** 2) + A_between_l0_norm * tf.reduce_sum(self.A_between ** 2)
        else:
            L0 = 0.0
            L2 += tf.reduce_sum(self.A_in ** 2) + tf.reduce_sum(self.A_between ** 2)
        b_regularization = tf.log(1 - (2 * self.b - 1) ** 2 + EPS)
        return L0 + L2 + b_regularization

    def transform(self, Input):
        """
        :param Input: (n_particles, batch_size, Dx + Dev)
        :param Dx: dimension of hidden space
        :return: output: (n_particles, batch_size, Dx)
        """
        # x_t + g_t + v_t * Wv + p_t * A

        A_in, g_in, Wv_in = tf.transpose(self.A_in, [1, 0]), self.g_in, tf.transpose(self.Wv_in, [1, 0])
        A_between, g_between, Wv_between = \
            tf.transpose(self.A_between, [1, 0]), self.g_between, tf.transpose(self.Wv_between, [1, 0])
        Dx = self.Dx

        x = Input[..., :Dx]  # (n_particles, batch_size, Dx)
        v = Input[0, 0:1, Dx:]  # (1, Dev)
        v_size = v.shape[-1]

        p_t = inverse_ilr_transform(x, self.psi)
        r_t = get_inode_relative_abundance(self.root, p_t, Dx)
        p_t = tf.concat([r_t, p_t], axis=-1)

        # (..., Dx + Dy, 1) * (Dx + Dy, Dx)
        pA = tf.reduce_sum(p_t[..., None] * (A_in + A_between), axis=-2)  # (..., Dx)

        delta = g_in + g_between + pA
        if v_size > 0:
            # Wv shape (Dev, Dx)
            Wvv = tf.reduce_sum(v[..., None] * (Wv_in + Wv_between), axis=-2)  # (n_particles, batch_size, Dx)
            delta += Wvv

        output = x + delta

        return output
