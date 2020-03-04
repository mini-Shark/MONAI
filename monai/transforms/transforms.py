# Copyright 2020 MONAI Consortium
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
A collection of "vanilla" transforms
https://github.com/Project-MONAI/MONAI/wiki/MONAI_Design
"""

import numpy as np
import torch
from scipy.ndimage.filters import gaussian_filter

import monai
from monai.data.utils import get_random_patch, get_valid_patch_size
from monai.transforms.compose import Randomizable
from monai.transforms.utils import (create_control_grid, create_grid, create_rotate, create_scale, create_shear,
                                    create_translate, rescale_array)
from monai.utils.misc import ensure_tuple

export = monai.utils.export("monai.transforms")


@export
class AddChannel:
    """
    Adds a 1-length channel dimension to the input image.
    """

    def __call__(self, img):
        return img[None]


@export
class Transpose:
    """
    Transposes the input image based on the given `indices` dimension ordering.
    """

    def __init__(self, indices):
        self.indices = indices

    def __call__(self, img):
        return img.transpose(self.indices)


@export
class Rescale:
    """
    Rescales the input image to the given value range.
    """

    def __init__(self, minv=0.0, maxv=1.0, dtype=np.float32):
        self.minv = minv
        self.maxv = maxv
        self.dtype = dtype

    def __call__(self, img):
        return rescale_array(img, self.minv, self.maxv, self.dtype)


@export
class ToTensor:
    """
    Converts the input image to a tensor without applying any other transformations.
    """

    def __call__(self, img):
        return torch.from_numpy(img)


@export
class UniformRandomPatch:
    """
    Selects a patch of the given size chosen at a uniformly random position in the image.
    """

    def __init__(self, patch_size):
        self.patch_size = (None,) + tuple(patch_size)

    def __call__(self, img):
        patch_size = get_valid_patch_size(img.shape, self.patch_size)
        slices = get_random_patch(img.shape, patch_size)

        return img[slices]


@export
class IntensityNormalizer:
    """Normalize input based on provided args, using calculated mean and std if not provided
    (shape of subtrahend and divisor must match. if 0, entire volume uses same subtrahend and
     divisor, otherwise the shape can have dimension 1 for channels).
     Current implementation can only support 'channel_last' format data.

    Args:
        subtrahend (ndarray): the amount to subtract by (usually the mean)
        divisor (ndarray): the amount to divide by (usually the standard deviation)
        dtype: output data format
    """

    def __init__(self, subtrahend=None, divisor=None, dtype=np.float32):
        if subtrahend is not None or divisor is not None:
            assert isinstance(subtrahend, np.ndarray) and isinstance(divisor, np.ndarray), \
                'subtrahend and divisor must be set in pair and in numpy array.'
        self.subtrahend = subtrahend
        self.divisor = divisor
        self.dtype = dtype

    def __call__(self, img):
        if self.subtrahend is not None and self.divisor is not None:
            img -= self.subtrahend
            img /= self.divisor
        else:
            img -= np.mean(img)
            img /= np.std(img)

        if self.dtype != img.dtype:
            img = img.astype(self.dtype)
        return img


@export
class ImageEndPadder:
    """Performs padding by appending to the end of the data all on one side for each dimension.
     Uses np.pad so in practice, a mode needs to be provided. See numpy.lib.arraypad.pad
     for additional details.

    Args:
        out_size (list): the size of region of interest at the end of the operation.
        mode (string): a portion from numpy.lib.arraypad.pad is copied below.
        dtype: output data format.
    """

    def __init__(self, out_size, mode, dtype=np.float32):
        assert out_size is not None and isinstance(out_size, (list, tuple)), 'out_size must be list or tuple'
        self.out_size = out_size
        assert isinstance(mode, str), 'mode must be str'
        self.mode = mode
        self.dtype = dtype

    def _determine_data_pad_width(self, data_shape):
        return [(0, max(self.out_size[i] - data_shape[i], 0)) for i in range(len(self.out_size))]

    def __call__(self, img):
        data_pad_width = self._determine_data_pad_width(img.shape[2:])
        all_pad_width = [(0, 0), (0, 0)] + data_pad_width
        img = np.pad(img, all_pad_width, self.mode)
        return img


@export
class Rotate90:
    """
    Rotate an array by 90 degrees in the plane specified by `axes`.
    """

    def __init__(self, k=1, axes=(1, 2)):
        """
        Args:
            k (int): number of times to rotate by 90 degrees.
            axes (2 ints): defines the plane to rotate with 2 axes.
        """
        self.k = k
        self.plane_axes = axes

    def __call__(self, img):
        return np.rot90(img, self.k, self.plane_axes)


@export
class RandRotate90(Randomizable):
    """
    With probability `prob`, input arrays are rotated by 90 degrees
    in the plane specified by `axes`.
    """

    def __init__(self, prob=0.1, max_k=3, axes=(1, 2)):
        """
        Args:
            prob (float): probability of rotating.
                (Default 0.1, with 10% probability it returns a rotated array)
            max_k (int): number of rotations will be sampled from `np.random.randint(max_k) + 1`.
                (Default 3)
            axes (2 ints): defines the plane to rotate with 2 axes.
                (Default (1, 2))
        """
        self.prob = min(max(prob, 0.0), 1.0)
        self.max_k = max_k
        self.axes = axes

        self._do_transform = False
        self._rand_k = 0

    def randomise(self):
        self._rand_k = self.R.randint(self.max_k) + 1
        self._do_transform = self.R.random() < self.prob

    def __call__(self, img):
        self.randomise()
        if not self._do_transform:
            return img
        rotator = Rotate90(self._rand_k, self.axes)
        return rotator(img)


class AffineGrid:
    """
    Affine transforms on the coordinates.
    """

    def __init__(self,
                 rotate_params=None,
                 shear_params=None,
                 translate_params=None,
                 scale_params=None,
                 as_tensor_output=True,
                 device=None):
        self.rotate_params = rotate_params
        self.shear_params = shear_params
        self.translate_params = translate_params
        self.scale_params = scale_params

        self.as_tensor_output = as_tensor_output
        self.device = device

    def __call__(self, spatial_size=None, grid=None):
        """
        Args:
            spatial_size (list or tuple of int): output grid size.
            grid (ndarray): grid to be transformed. Shape must be (3, H, W) for 2D or (4, H, W, D) for 3D.
        """
        if grid is None:
            if spatial_size is not None:
                grid = create_grid(spatial_size)
            else:
                raise ValueError('Either specify a grid or a spatial size to create a grid from.')

        spatial_dims = len(grid.shape) - 1
        affine = np.eye(spatial_dims + 1)
        if self.rotate_params:
            affine = affine @ create_rotate(spatial_dims, self.rotate_params)
        if self.shear_params:
            affine = affine @ create_shear(spatial_dims, self.shear_params)
        if self.translate_params:
            affine = affine @ create_translate(spatial_dims, self.translate_params)
        if self.scale_params:
            affine = affine @ create_scale(spatial_dims, self.scale_params)
        affine = torch.tensor(affine, device=self.device)

        if not torch.is_tensor(grid):
            grid = torch.tensor(grid)
        if self.device:
            grid = grid.to(self.device)
        grid_spatial = list(grid.shape[1:])
        grid = grid.reshape((grid.shape[0], -1))
        grid = (affine @ grid).reshape([-1] + grid_spatial)
        if self.as_tensor_output:
            return grid
        return grid.cpu().numpy()


class RandAffineGrid(Randomizable):
    """
    generate randomised affine grid
    """

    def __init__(self,
                 rotate_range=None,
                 shear_range=None,
                 translate_range=None,
                 scale_range=None,
                 as_tensor_output=True,
                 device=None):
        self.rotate_range = rotate_range
        self.shear_range = shear_range
        self.translate_range = translate_range
        self.scale_range = scale_range

        self.rotate_params = None
        self.shear_params = None
        self.translate_params = None
        self.scale_params = None

        self.as_tensor_output = as_tensor_output
        self.device = device

    def randomise(self):
        if self.rotate_range:
            self.rotate_params = [self.R.uniform(-f, f) for f in ensure_tuple(self.rotate_range)]
        if self.shear_range:
            self.shear_params = [self.R.uniform(-f, f) for f in ensure_tuple(self.shear_range)]
        if self.translate_range:
            self.translate_params = [self.R.uniform(-f, f) for f in ensure_tuple(self.translate_range)]
        if self.scale_range:
            self.scale_params = [self.R.uniform(-f, f) for f in ensure_tuple(self.scale_range)]

    def __call__(self, spatial_size=None, grid=None):
        self.randomise()
        _affine_grid = AffineGrid(self.rotate_params, self.shear_params, self.translate_params, self.scale_params,
                                  self.as_tensor_output, self.device)
        return _affine_grid(spatial_size, grid)


class RandDeformGrid(Randomizable):
    """
    generate random deformation grid
    """

    def __init__(self, spacing, magnitude_range, as_tensor_output=True, device=None):
        self.spacing = spacing
        self.magnitude = magnitude_range

        self.rand_mag = 1.0
        self.as_tensor_output = as_tensor_output
        self.device = device

    def randomise(self, grid_size):
        self.random_offset = self.R.normal(size=([len(grid_size)] + list(grid_size)))
        self.rand_mag = self.R.uniform(self.magnitude[0], self.magnitude[1])

    def __call__(self, spatial_size):
        control_grid = create_control_grid(spatial_size, self.spacing)
        self.randomise(control_grid.shape[1:])
        control_grid[:len(spatial_size)] += self.rand_mag * self.random_offset
        if self.as_tensor_output:
            control_grid = torch.tensor(control_grid, device=self.device)
        return control_grid


class Resample:

    def __init__(self, padding_mode='zeros', as_tensor_output=False, device=None):
        """
        computes output image using values from `img`, locations from `grid` using pytorch.

        Args:
            padding_mode ('zeros'|'border'|'reflection'): mode of handling out of range indices. Defaults to 'zeros'.
            as_tensor_output(bool): whether to return a torch tensor. Defaults to False.
            device (string):
        """
        self.padding_mode = padding_mode
        self.as_tensor_output = as_tensor_output
        self.device = device

    def __call__(self, img, grid, mode='bilinear'):
        """
        Args:
            img (ndarray or tensor): shape must be (num_channels, H, W[, D]).
            grid (ndarray or tensor): shape must be (3, H, W) for 2D or (4, H, W, D) for 3D.
            mode ('nearest'|'bilinear'): interpolation order. Defaults to 'bilinear'.
        """
        if not torch.is_tensor(img):
            img = torch.tensor(img)
        if not torch.is_tensor(grid):
            grid = torch.tensor(grid)
        if self.device:
            img = img.to(self.device)
            grid = grid.to(self.device)

        for i, dim in enumerate(img.shape[1:]):
            grid[i] = 2. * grid[i] / (dim - 1.)
        grid = grid[:-1] / grid[-1:]
        grid = grid[range(img.ndim - 2, -1, -1)]
        grid = grid.permute(list(range(grid.ndim))[1:] + [0])
        new_img = torch.nn.functional.grid_sample(img[None].float(),
                                                  grid[None].float(),
                                                  mode=mode,
                                                  padding_mode=self.padding_mode)[0]
        if not self.as_tensor_output:
            return new_img.cpu().numpy()
        return new_img


@export
class Affine:
    """
    transform ``img`` given the affine parameters.
    """

    def __init__(self,
                 rotate_params=None,
                 shear_params=None,
                 translate_params=None,
                 scale_params=None,
                 padding_mode='zeros',
                 as_tensor_output=False,
                 device=None):
        self.affine_grid = AffineGrid(rotate_params,
                                      shear_params,
                                      translate_params,
                                      scale_params,
                                      as_tensor_output=True,
                                      device=device)
        self.resampler = Resample(padding_mode, as_tensor_output=as_tensor_output, device=device)

    def __call__(self, img, spatial_size, mode='bilinear'):
        """
        Args:
            img (ndarray or tensor): shape must be (num_channels, H, W[, D]),
                spatial rank must be len(self.spatial_size).
            spatial_size (list or tuple of int): output grid size.
            mode ('nearest'|'bilinear'): interpolation order. Defaults to 'bilinear'.
        """
        grid = self.affine_grid(spatial_size)
        return self.resampler(img, grid, mode)


@export
class RandAffine(Randomizable):
    """
    Random affine transform.
    """

    def __init__(self,
                 prob=0.1,
                 rotate_range=None,
                 shear_range=None,
                 translate_range=None,
                 scale_range=None,
                 padding_mode='zeros',
                 as_tensor_output=True,
                 device=None):
        """
        Args:
            prob (float): probability of returning a randomized affine grid.
                defaults to 0.1, with 10% chance returns a randomized grid.
        """

        self.rand_affine_grid = RandAffineGrid(rotate_range, shear_range, translate_range, scale_range, True, device)
        self.resampler = Resample(padding_mode=padding_mode, as_tensor_output=as_tensor_output, device=device)

        self.do_transform = False
        self.prob = prob

        self.as_tensor_output = as_tensor_output
        self.device = device

    def randomise(self):
        self.do_transform = self.R.rand() < self.prob

    def __call__(self, img, spatial_size, mode='bilinear'):
        self.randomise()
        if self.do_transform:
            grid = self.rand_affine_grid(spatial_size=spatial_size)
        else:
            grid = torch.tensor(create_grid(spatial_size), device=self.device)
        return self.resampler(img, grid, mode)


@export
class Rand2DElastic(Randomizable):
    """
    Random elastic deformation and affine in 2D
    """

    def __init__(self,
                 spacing,
                 magnitude_range,
                 prob=0.1,
                 rotate_range=None,
                 shear_range=None,
                 translate_range=None,
                 scale_range=None,
                 padding_mode='zeros',
                 as_tensor_output=False,
                 device=None):
        self.deform_grid = RandDeformGrid(spacing, magnitude_range, as_tensor_output=True, device=device)
        self.rand_affine_grid = RandAffineGrid(rotate_range, shear_range, translate_range, scale_range, True, device)
        self.resampler = Resample(padding_mode=padding_mode, as_tensor_output=as_tensor_output, device=device)

        self.prob = prob
        self.as_tensor_output = as_tensor_output
        self.device = device
        self.do_transform = False

    def randomise(self):
        self.do_transform = self.R.rand() < self.prob

    def __call__(self, img, spatial_size, mode='bilinear'):
        self.randomise()
        if self.do_transform:
            grid = self.deform_grid(spatial_size)
            grid = self.rand_affine_grid(grid=grid)
            grid = torch.nn.functional.interpolate(grid[None], spatial_size, mode='bicubic')[0]
        else:
            grid = torch.tensor(create_grid(spatial_size), device=self.device)
        return self.resampler(img, grid, mode)


@export
class Rand3DElastic(Randomizable):
    """
    Random elastic deformation and affine in 3D
    """

    def __init__(self,
                 alpha_range,
                 sigma_range,
                 prob=0.1,
                 rotate_range=None,
                 shear_range=None,
                 translate_range=None,
                 scale_range=None,
                 padding_mode='zeros',
                 as_tensor_output=False,
                 device=None):
        self.rand_affine_grid = RandAffineGrid(rotate_range, shear_range, translate_range, scale_range, True, device)
        self.resampler = Resample(padding_mode=padding_mode, as_tensor_output=as_tensor_output, device=device)

        self.prob = prob
        self.as_tensor_output = as_tensor_output
        self.device = device

        self.do_transform = False
        self.rand_offset = None
        self.alpha = 1.0
        self.sigma = 1.0

    def randomise(self, grid_size):
        self.do_transform = self.R.rand() < self.prob
        if self.do_transform:
            self.rand_offset = self.R.random.uniform(-1., 1., [3] + list(grid_size))
        self.alpha = self.R.random.uniform(self.alpha_range[0], self.alpha_range[1])
        self.sigma = self.R.random.uniform(self.sigma_range[0], self.sigma_range[1])

    def __call__(self, img, spatial_size, mode='bilinear'):
        if self.do_transform:
            grid = create_grid(spatial_size)
            self.randomise(grid.shape[1:])
            for i in range(3):
                grid[i] += gaussian_filter(self.rand_offset[i], self.sigma, mode='constant', cval=0) * self.alpha
            grid = self.rand_affine_grid(grid=grid)
        else:
            grid = torch.tensor(create_grid(spatial_size), device=self.device)
        return self.resampler(img, grid, mode)


if __name__ == "__main__":
    # img = np.array((1, 2, 3, 4)).reshape((1, 2, 2))
    # rotator = RandRotate90(prob=0.0, max_k=3, axes=(1, 2))
    # # rotator.set_random_state(1234)
    # img_result = rotator(img)
    # print(type(img))
    # print(img_result)

    # np_im = np.zeros((3, 1201, 1601))
    # np_im = np_img
    np_im = np.zeros((3, 80, 80, 80))

    # new_img = Affine(translate_params=[-200, 300], scale_params=(1.2, 1.2))(np_img, (300, 400))
    # new_img = Rand2DElastic(prob=1.0, spacing=(20, 20), magnitude_range=(1.0, 4.0), translate_range=[400., 400.])(
    #     np_img, (300, 400))
    new_img = Rand3DElastic(prob=1.0, alpha_range=(1.0, 4.0), sigma_range=(1., 4.), translate_range=[20., 30., 10.])(
        np_im, (30, 40, 50))
    print(new_img.shape)
    # new_img = np.moveaxis(new_img, 0, -1).astype(int)
    # plt.imshow(new_img)
    # plt.show()