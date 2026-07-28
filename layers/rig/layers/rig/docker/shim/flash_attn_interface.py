"""flash-attn's interface, backed by torch SDPA, so gfx1151 needs no CUDA kernel.

Four modules in SkinTokens open with

    try:    from flash_attn_interface import flash_attn_func
    except: from flash_attn.flash_attn_interface import flash_attn_func as _f

and there is no third branch, so on a machine without flash-attn the import
fails and nothing loads. Putting this module on the path makes the first branch
succeed and leaves their source untouched, which matters because the day they
retag, a patched checkout is a merge and a shim is nothing.

The implementation is not invented here: `src/model/skin_vae/attention_processor.py`
already carries it as its own fallback. This is that function, promoted to the
name the other modules look for.

flash-attn lays tensors out as [B, S, H, D] and torch SDPA wants [B, H, S, D],
so the permutes are the whole adapter. The repeat_interleave covers grouped
query attention, where K and V carry fewer heads than Q and flash-attn expands
them internally.
"""

import torch


def flash_attn_func(q, k, v, *args, **kwargs):
    """[B, S, H, D] in, ([B, S, H, D], None) out. The None is the LSE flash-attn returns."""
    q = q.permute(0, 2, 1, 3)
    k = k.permute(0, 2, 1, 3)
    v = v.permute(0, 2, 1, 3)

    if q.shape[1] != k.shape[1]:
        repeat = q.shape[1] // k.shape[1]
        k = k.repeat_interleave(repeat, dim=1)
        v = v.repeat_interleave(repeat, dim=1)

    out = torch.nn.functional.scaled_dot_product_attention(
        q, k, v, is_causal=bool(kwargs.get("causal", False)))
    return out.permute(0, 2, 1, 3), None


__all__ = ["flash_attn_func"]
