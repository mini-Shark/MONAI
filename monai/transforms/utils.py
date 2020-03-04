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

import random

import numpy as np


def rand_choice(prob=0.5):
    """Returns True if a randomly chosen number is less than or equal to `prob', by default this is a 50/50 chance."""
    return random.random() <= prob


def img_bounds(img):
    """Returns the minimum and maximum indices of non-zero lines in axis 0 of `img', followed by that for axis 1."""
    ax0 = np.any(img, axis=0)
    ax1 = np.any(img, axis=1)
    return np.concatenate((np.where(ax0)[0][[0, -1]], np.where(ax1)[0][[0, -1]]))


def in_bounds(x, y, margin, maxx, maxy):
    """Returns True if (x,y) is within the rectangle (margin,margin,maxx-margin,maxy-margin)."""
    return margin <= x < (maxx - margin) and margin <= y < (maxy - margin)


def is_empty(img):
    """Returns True if `img' is empty, that is its maximum value is not greater than its minimum."""
    return not (img.max() > img.min())  # use > instead of <= so that an image full of NaNs will result in True


def ensure_tuple_size(tup, dim):
    """Returns a copy of `tup' with `dim' values by either shortened or padded with zeros as necessary."""
    tup = tuple(tup) + (0,) * dim
    return tup[:dim]


def zero_margins(img, margin):
    """Returns True if the values within `margin' indices of the edges of `img' in dimensions 1 and 2 are 0."""
    if np.any(img[:, :, :margin]) or np.any(img[:, :, -margin:]):
        return False

    if np.any(img[:, :margin, :]) or np.any(img[:, -margin:, :]):
        return False

    return True


def rescale_array(arr, minv=0.0, maxv=1.0, dtype=np.float32):
    """Rescale the values of numpy array `arr' to be from `minv' to `maxv'."""
    if dtype is not None:
        arr = arr.astype(dtype)

    mina = np.min(arr)
    maxa = np.max(arr)

    if mina == maxa:
        return arr * minv

    norm = (arr - mina) / (maxa - mina)  # normalize the array first
    return (norm * (maxv - minv)) + minv  # rescale by minv and maxv, which is the normalized array by default


def rescale_instance_array(arr, minv=0.0, maxv=1.0, dtype=np.float32):
    """Rescale each array slice along the first dimension of `arr' independently."""
    out = np.zeros(arr.shape, dtype)
    for i in range(arr.shape[0]):
        out[i] = rescale_array(arr[i], minv, maxv, dtype)

    return out


def rescale_array_int_max(arr, dtype=np.uint16):
    """Rescale the array `arr' to be between the minimum and maximum values of the type `dtype'."""
    info = np.iinfo(dtype)
    return rescale_array(arr, info.min, info.max).astype(dtype)


def copypaste_arrays(src, dest, srccenter, destcenter, dims):
    """
    Calculate the slices to copy a sliced area of array `src' into array `dest'. The area has dimensions `dims' (use 0
    or None to copy everything in that dimension), the source area is centered at `srccenter' index in `src' and copied
    into area centered at `destcenter' in `dest'. The dimensions of the copied area will be clipped to fit within the
    source and destination arrays so a smaller area may be copied than expected. Return value is the tuples of slice
    objects indexing the copied area in `src', and those indexing the copy area in `dest'.

    Example:
        src=np.random.randint(0,10,(6,6))
        dest=np.zeros_like(src)
        srcslices,destslices=copypasteArrays(src,dest,(3,2),(2,1),(3,4))
        dest[destslices]=src[srcslices]
        print(src)
        print(dest)

        >>> [[9 5 6 6 9 6]
             [4 3 5 6 1 2]
             [0 7 3 2 4 1]
             [3 0 0 1 5 1]
             [9 4 7 1 8 2]
             [6 6 5 8 6 7]]
            [[0 0 0 0 0 0]
             [7 3 2 4 0 0]
             [0 0 1 5 0 0]
             [4 7 1 8 0 0]
             [0 0 0 0 0 0]
             [0 0 0 0 0 0]]
    """
    srcslices = [slice(None)] * src.ndim
    destslices = [slice(None)] * dest.ndim

    for i, ss, ds, sc, dc, dim in zip(range(src.ndim), src.shape, dest.shape, srccenter, destcenter, dims):
        if dim:
            # dimension before midpoint, clip to size fitting in both arrays
            d1 = np.clip(dim // 2, 0, min(sc, dc))
            # dimension after midpoint, clip to size fitting in both arrays
            d2 = np.clip(dim // 2 + 1, 0, min(ss - sc, ds - dc))

            srcslices[i] = slice(sc - d1, sc + d2)
            destslices[i] = slice(dc - d1, dc + d2)

    return tuple(srcslices), tuple(destslices)


def resize_center(img, *resize_dims, fill_value=0):
    """
    Resize `img' by cropping or expanding the image from the center. The `resizeDims' values are the output dimensions
    (or None to use original dimension of `img'). If a dimension is smaller than that of `img' then the result will be
    cropped and if larger padded with zeros, in both cases this is done relative to the center of `img'. The result is
    a new image with the specified dimensions and values from `img' copied into its center.
    """
    resize_dims = tuple(resize_dims[i] or img.shape[i] for i in range(len(resize_dims)))

    dest = np.full(resize_dims, fill_value, img.dtype)
    half_img_shape = np.asarray(img.shape) // 2
    half_dest_shape = np.asarray(dest.shape) // 2

    srcslices, destslices = copypaste_arrays(img, dest, half_img_shape, half_dest_shape, resize_dims)
    dest[destslices] = img[srcslices]

    return dest


def one_hot(labels, num_classes):
    """
    Converts label image `labels' to a one-hot vector with `num_classes' number of channels as last dimension.
    """
    labels = labels % num_classes
    y = np.eye(num_classes)
    onehot = y[labels.flatten()]

    return onehot.reshape(tuple(labels.shape) + (num_classes,)).astype(labels.dtype)


def create_grid(spatial_size, spacing=None, homogeneous=True):
    """
    compute a `spatial_size` mesh.
    Args:
        spatial_size (sequence of ints): spatial size of the grid.
        spacing (sequence of ints): same len as ``spatial_size``, defaults to 1.0 (dense grid).
        homogeneous (bool): whether to make homogeneous coordinates.
    """
    spacing = spacing or tuple(1.0 for _ in spatial_size)
    ranges = [np.linspace(-(d - 1.) / 2. * s, (d - 1.) / 2. * s, int(d)) for d, s in zip(spatial_size, spacing)]
    coords = np.asarray(np.meshgrid(*ranges, indexing='ij'), dtype=float)
    if not homogeneous:
        return coords
    return np.concatenate([coords, np.ones_like(coords[0:1, ...])])


def create_control_grid(spatial_shape, spacing):
    grid_shape = []
    for s, t in zip(spatial_shape, spacing):
        if s % 2 == 0:
            grid_shape.append(np.ceil((s - 1.) / (2. * t) + 0.5) * 2. + 2.)
        else:
            grid_shape.append(np.ceil((s - 1.) / (2. * t)) * 2. + 3.)
    return create_grid(grid_shape, spacing)


def create_rotate(spatial_dims, radians):
    """
    create a 2D or 3D rotation matrix
    Args:
        spatial_dims (int): spatial rank
        radians (float or a sequence of floats): rotation radians
    """
    radians = tuple(radians)
    if spatial_dims == 2:
        if len(radians) >= 1:
            sin_, cos_ = np.sin(radians[0]), np.cos(radians[0])
            return np.array([[cos_, -sin_, 0.], [sin_, cos_, 0.], [0., 0., 1.]])

    if spatial_dims == 3:
        affine = None
        if len(radians) >= 1:
            sin_, cos_ = np.sin(radians[0]), np.cos(radians[0])
            affine = np.array([
                [1., 0., 0., 0.],
                [0., cos_, -sin_, 0.],
                [0., sin_, cos_, 0.],
                [0., 0., 0., 1.],
            ])
        if len(radians) >= 2:
            sin_, cos_ = np.sin(radians[1]), np.cos(radians[1])
            affine = affine @ np.array([
                [cos_, 0.0, sin_, 0.],
                [0., 1., 0., 0.],
                [-sin_, 0., cos_, 0.],
                [0., 0., 0., 1.],
            ])
        if len(radians) >= 3:
            sin_, cos_ = np.sin(radians[2]), np.cos(radians[2])
            affine = affine @ np.array([
                [cos_, -sin_, 0., 0.],
                [sin_, cos_, 0., 0.],
                [0., 0., 1., 0.],
                [0., 0., 0., 1.],
            ])
        return affine

    raise ValueError('create_rotate got spatial_dims={}, radians={}.'.format(spatial_dims, radians))


def create_shear(spatial_dims, coefs):
    """
    create a shearing matrix
    Args:
        spatial_dims (int): spatial rank
        coefs (floats): shearing factors, defaults to 0.
    """
    coefs = list(coefs)
    if spatial_dims == 2:
        while len(coefs) < 2:
            coefs.append(0.0)
        return np.array([
            [1, coefs[0], 0.],
            [coefs[1], 1., 0.],
            [0., 0., 1.],
        ])
    if spatial_dims == 3:
        while len(coefs) < 6:
            coefs.append(0.0)
        return np.array([
            [1., coefs[0], coefs[1], 0.],
            [coefs[2], 1., coefs[3], 0.],
            [coefs[4], coefs[5], 1., 0.],
            [0., 0., 0., 1.],
        ])
    raise NotImplementedError


def create_scale(spatial_dims, s):
    """
    create a scaling matrix
    Args:
        spatial_dims (int): spatial rank
        s (floats): scaling factors, defaults to 1.
    """
    s = list(s)
    while len(s) < spatial_dims:
        s.append(1.)
    return np.diag(s[:spatial_dims] + [1.])


def create_translate(spatial_dims, t):
    """
    create a translation matrix
    Args:
        spatial_dims (int): spatial rank
        t (floats): translate factors, defaults to 0.
    """
    affine = np.eye(spatial_dims + 1)
    for i, a in enumerate(t[:spatial_dims]):
        affine[i, spatial_dims] = a
    return affine