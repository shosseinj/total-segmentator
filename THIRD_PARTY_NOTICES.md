# Upstream projects

This private deployment bundle contains the service implementation and selected prepared CT checkpoints. Upstream code, packages and weights retain their respective licenses and terms; this file does not replace them.

- TotalSegmentator 2.11.0: https://github.com/wasserth/TotalSegmentator/tree/v2.11.0
- nnU-Net: https://github.com/MIC-DKFZ/nnUNet
- PyTorch: https://pytorch.org/
- highdicom: https://github.com/ImagingDataCommons/highdicom
- RevCT 0.3.2: https://github.com/hcltech-robotics/rev-ct/tree/116686e77ea857dfc4d93fe366e7da04b4214b92
  Code and trained heads: Apache-2.0. Bundled Microsoft COLIPRI encoder: MIT.
  Public weights: https://huggingface.co/hcltech-robotics/rev-ct/tree/706dbf5f1f9fee7f332955d77cab15bef24409ea
  The downloaded model directory retains LICENSE, NOTICE and COLIPRI_LICENSE.
- Optional CT-CLIP / CTViT and ClassFine (CT_LiPro_v2.pt): https://github.com/ibrahimethemhamamci/CT-CLIP/tree/a2a155c601987820433c01db69b64d701d3d229d
  Released by its authors under CC-BY-NC-SA 4.0 for non-commercial research.
  The service uses the upstream image encoder and reproduces its image-only
  ClassFine head and preprocessing in app/inference/ct_clip_process.py.

Package licenses are also available inside the built Python image. `models/checkpoint-manifest.json` records TotalSegmentator checksums. `models/rev-ct/checkpoint-manifest.json` and `source.json` record RevCT checksums and pinned provenance. Abnormality models produce unreviewed chest CT findings alongside CT total segmentation.
