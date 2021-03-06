import torch
import numpy as np
from matplotlib import pyplot as plt
from datetime import datetime

from lafomo.utilities.torch import is_cuda, discretise
from lafomo.models import PartialLFM
from lafomo.plot import plot_before_after
from .variational import VariationalTrainer


class PDETrainer(VariationalTrainer):

    def __init__(self, lfm: PartialLFM, optimizer: torch.optim.Optimizer, dataset, **kwargs):
        super().__init__(lfm, optimizer, dataset, **kwargs)
        self.debug_iteration = 0
        data = next(iter(dataset))
        data_input, y = data
        data_input = data_input.cuda() if is_cuda() else data_input
        y = y.cuda() if is_cuda() else y
        tx, y_target = data_input, y

        # 1. Ensure that time axis must be in ascending order:
        # We use mergesort to maintain relative order
        self.t_sorted = np.argsort(tx[0, :], kind='mergesort')
        tx = tx[:, self.t_sorted]
        y_target = y_target[:, self.t_sorted]

        # 2. Discretise time
        if hasattr(dataset, 'num_discretised'):
            num_discretised = dataset.num_discretised
        else:
            num_discretised = 40
        time = discretise(tx[0, :], num_discretised=num_discretised)
        time = torch.tensor(time)
        # 3. Discretise space
        spatial_grid = self.discretise_spatial(tx)
        spatial = torch.tensor(spatial_grid)

        # 4. Reconstruct dataset
        new_t = time.repeat(spatial.shape[0], 1).transpose(0, 1).reshape(-1)
        t_mask = new_t == tx[0, :]

        new_x = spatial.repeat(time.shape[0])
        x_mask = new_x == tx[1, :]

        mask = torch.stack([t_mask, x_mask])
        self.tx = torch.stack([new_t, new_x])
        self.y_target = y_target

    def discretise_spatial(self, tx):
        # whilst maintaining an inverse mapping to mask the output for
        # calculating the loss. shape (T, X_unique).
        # For each time, for which spatial points do we have data for
        spatial = np.unique(tx[1, :])
        range = spatial[-1] - spatial[0]
        x_dp = spatial[1] - spatial[0]
        print('x dp is set to', x_dp)
        num_discretised = int(range / x_dp)
        spatial_grid = discretise(spatial, num_discretised=num_discretised)
        return spatial_grid

    def single_epoch(self, step_size=1e-1):
        self.optimizer.zero_grad()
        y = self.y_target
        output = self.lfm(self.tx, step_size=step_size)
        self.debug_out(self.tx, y, output)

        log_likelihood, kl_divergence, _ = self.lfm.loss_fn(output, y.permute(1, 0))

        loss = - (log_likelihood - kl_divergence)

        loss.backward()
        self.optimizer.step()
        return loss.item(), (-log_likelihood.item(), kl_divergence.item())

    def debug_out(self, data_input, y_target, output):
        if (self.debug_iteration % 10) != 0:
            self.debug_iteration += 1
            return
        self.debug_iteration += 1
        print(output.variance.max(), output.mean.shape, output.variance.shape)
        ts = self.tx[0, :].unique().numpy()
        xs = self.tx[1, :].unique().numpy()
        extent = [ts[0], ts[-1], xs[0], xs[-1]]

        num_t = ts.shape[0]
        num_x = xs.shape[0]
        f_mean = output.mean.reshape(num_t, num_x).detach()
        y_target = y_target.reshape(num_t, num_x)
        axes = plot_before_after(
            f_mean.transpose(0, 1),
            y_target.transpose(0, 1),
            extent, ['Prediction', 'Ground truth']
        )
        xy = self.lfm.inducing_points.detach()[0]
        axes[0].scatter(xy[:, 0], xy[:, 1], facecolors='none', edgecolors='r', s=3)

        plt.savefig(str(datetime.now().timestamp()) + '.png')

    def print_extra(self):
        print(' s:', self.lfm.fenics_parameters[0][0].item(),
              'dif:', self.lfm.fenics_parameters[1][0].item(),
              'dec:', self.lfm.fenics_parameters[2][0].item())

