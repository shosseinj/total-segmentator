# Upstream projects

This private deployment bundle contains the service implementation and selected prepared CT checkpoints. Upstream code, packages and weights retain their respective licenses and terms; this file does not replace them.

- TotalSegmentator 2.11.0: https://github.com/wasserth/TotalSegmentator/tree/v2.11.0
- nnU-Net: https://github.com/MIC-DKFZ/nnUNet
- PyTorch: https://pytorch.org/
- highdicom: https://github.com/ImagingDataCommons/highdicom

Package licenses are also available inside the built Python image. `models/checkpoint-manifest.json` records the immutable checkpoint file checksums used by this service. Only CT total, selected structures, is enabled here.
