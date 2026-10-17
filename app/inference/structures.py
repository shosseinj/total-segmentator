"""Anatomical label IDs from the installed, pinned TotalSegmentator model."""
import colorsys
from totalsegmentator.map_to_binary import class_map

TOTAL_LABELS={int(number):name for number,name in class_map['total'].items()}
TOTAL_STRUCTURES=set(TOTAL_LABELS.values())


def normalize_structures(structures):
    values=set(structures)
    if values=={'all'}:return sorted(TOTAL_STRUCTURES)
    if not values or values-TOTAL_STRUCTURES:raise ValueError('INVALID_CONFIG')
    return sorted(values)


def label_colors(labels):
    return {int(number):tuple(round(c*255) for c in colorsys.hsv_to_rgb((int(number)*0.61803398875)%1,.68,.98)) for number in labels}
