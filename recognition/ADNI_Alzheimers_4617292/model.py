#!/usr/bin/env python3

# pi/log for Fourier encoding
from math import pi, log

import torch
import torch.nn as nn


"""
    Unofficial implementation of the Perceiver Transformer model by Deepmind.

    The perceiver consists of two components:
        - The cross attention layer
        - The latent transformer layer

    The components are alternated one after another.
"""

__author__ = "Chegne Eu Joe"
__email__ = "e.chegne@uqconnect.edu.au"


"""
def fourier_encode(x, max_freq, num_bands=4):
    Allows parameterized fourier feature positional encodings which:

    1. Directly represents the position structure of the input data (To compensate for the lack of explicit grid structures).
    2. Control the number of frequency bands in position encoding independent of the cutoff frequency.
    3. Uniformly sample all frequencies up to a target resolution.

    Args:
        x - input
        max_freq - maximmum frequency
        num_bands - size of constructed tensor
        base - base of logarithm function

    Returns:
        Fourier encoded input x.

    x = x.unsqueeze(-1)
    device, dtype, orig_x = x.device, x.dtype, x

    scales = torch.logspace(
        start=1.0,
        log(max_freq / 2) / log(10),
        device=device,
        dtype=dtype,
    )

    scales = scales[(*((None,) * len(x.shape) - 1)), Ellipsis)]
    x = x * scales * pi
    x = torch.cat([x.sin(), x.cos()], dim=1)
    x= torch.cat((x, orig_x), dim=-1)

    return x

"""


class PreNorm(nn.Module):
    """
    A wrapper used to normalize values before each procedure using LayerNorm.

    Args:
        dim - input dimension.
        fn - Layer to be applied post normalization.
        context_dim - Normalizes the context dimension (of fn) if available. Used for cross attention layers.
    Returns:
        Layer with normalized values
    """

    def __init__(self, dim, fn, context_dim=None):
        super().__init__()
        self.fn = fn
        self.norm = nn.LayerNorm(dim)
        if context_dim is not None:
            self.norm_context = nn.LayerNorm(context_dim)
        else:
            self.norm_context = None

    def forward(self, x, **kwargs):
        x = self.norm(x)

        if self.norm_context is not None:
            context = kwargs["x_kv"]
            normed_context = self.norm_context(context)
            kwargs.update(context=normed_context)

        return self.fn(x, **kwargs)


class Attention(nn.Module):
    """
    Allows the model to jointly attend to information from different
    representation subspaces.

    Wrapper for {self/cross}-attention layers which will be further
    elaborated in the respective classes

    See https://arxiv.org/abs/1706.03762 for more details.

    Args:
        embed_dim - Total dimension of model.
        num_heads - Number of parallel attention heads.
        dropout - Dropout probability.
    Returns:
        Attention outputs of shape (N, L, E) where L is the target
        sequence length, N is the batch size, and E is embed_dim
    """

    def __init__(self, embed_dim, heads=8, dropout=0.0):
        super().__init__()
        self.attention = nn.MultiHeadAttention(
            latent_dim, heads, dropout, bias=True, batch_first=True
        )

    def forward(self, x, residual):
        attn_output, _ = self.attention(residual, x, x)
        return attn_output + residual


class SelfAttention(nn.Module):
    """
    For each input vector v_i, compute the output o_i by taking the
    weighted sum of all non-query input vectors, where the weight is proportional
    exp(v_i * v_j / sqrt(d)), dot product.

    This allows us to measure similarity between two vectors. The higher the weight,
    the more similar the input sequence is compared to the query and vice versa.

    See https://arxiv.org/abs/1706.03762 for more details.
    """

    def __init__(self, embed_dim, heads=8, dropout=0.0):
        super().__init__()
        self.attention = PreNorm(embed_dim, Attention(embed_dim, heads, dropout))

    def forward(self, x, residual):
        # Self-attention
        return self.attention(x, x)


class CrossAttention(nn.Module):
    """
    Similar to self attention. However in cross attention,
    queries are generated from a separate embedding to the keys and values.
    Hence the name cross attention.

    In this implementation, embed_dim is determined by q_channels for simplification.
    A non-simplified CrossAttention layer will require building the attention layer
    from the ground up, which takes too much time, so I'll leave it as a TODO.

    See https://arxiv.org/abs/1706.03762 for more details.
    """

    def __init__(self, embed_dim, heads=1, dropout=0.0):
        super().__init__()
        self.attention = PreNorm(embed_dim, Attention(embed_dim, heads, dropout))

    def forward(x, residual):
        return self.attention(x, residual)


class GEGLU(nn.Module):
    def forward(self, x):
        x, gates = x.chunk(2, dim=-1)
        return x * F.gelu(gates)


class MLP(nn.Module):
    def __init__(self, latent_dim, dropout=0.0):
        super().__init__()
        self.net = nn.Sequential(
            nn.LayerNorm(latent_dim),
            nn.Linear(latent_dim, latent_dim),
            GEGLU(),
            nn.Dropout(dropout),
            nn.Linear(latent_dim, latent_dim),
        )

    def forward(self, x):
            return self.net(x)


class Perceiver(nn.Module):
    """
    A scalable, fully attentional architecture.
    Note that data has to be pre-fourier encoded or it will not work.

    Args:
        depth - The depth of the network. See code for more information.
        num_channels - Number of channels of each input.
        input_shape - Size & shape of input.
        fourier_bands - Number of bands for fourier encoding.
        num_latents - Number of latent vectors.
        latent_dim - Latent dimension.
        latent_heads - Number of heads for self attention.
        attn_dropout - Attention dropout probability.
        ff_dropout - MLP dropout probability.
        num_features - How many different classes for output
        self_per_cross_attn - Number of self attention blocks per cross attention.
    Returns:
        Perceiver layer
    """

    def __init__(
        self,
        depth,
        num_channels,
        input_shape,
        fourier_bands,
        num_latents,
        latent_dim,
        latent_heads=8,
        attn_dropout=0.0,
        ff_dropout=0.0,
        num_features=2,
        self_per_cross_attn=3,
    ):
        super().__init__()

        # Initial latent vectorss
        self.latents = nn.Parameter(torch.randn(num_latents, latent_dim))
        self.depth = depth

        # Build architecture based on params
        # Depth * (cross attention layer + (self_per_cross_attn * self attention layer))
        self.layers = nn.ModuleList([])
        for i in range(depth):
            self_attns = nn.ModuleList([])

            # Construct self attention block
            for _ in range(self_per_cross_attn):
                self_attns.append(
                    nn.ModuleList([latent_attn_layer(), latent_ff_layer()])
                )

            # Construct one perceiver block
            self.layers.append(
                nn.Modulelist([cross_attn_layer(), cross_ff_layer(), self_attns])
            )

        def forward(self, data):
            # TODO Fourier encode, possibly preprocess data prior to sending to perceiver.

            for cross_attn, cross_ff, self_attns in self.layers:
                x = cross_attn(x, context=data, mask=mask) + x
                x = cross_ff(x) + x

                for self_attn, self_ff in self_attns:
                    x = self_attn(x) + x
                    x = self_ff(x) + x

            return self.to_logits(x)
