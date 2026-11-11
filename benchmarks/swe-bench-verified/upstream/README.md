# Upstream label files

Copied (or derived) from public, permissively licensed sources. Attribution:

- `paichecker_verified_labels.csv`: PAIChecker (MIT), labelled_data/ - instance_id + label columns only (the upstream file is 6.8 MB with model outputs) Source: https://github.com/manyiResearch/PAIChecker/tree/main/labelled_data. sha256 `9eff20c2399e4b578d512737b9513c96dc7662fd86899b16137d98f0b39c6e61`.
- `utboost_changed_ids.txt`: derived: instances whose tests UTBoost augmented, by diffing uiuc-kang-lab/SWE-bench-{Verified,Lite}-UTBoost against the originals (UTBoost code: MIT). Not a list the authors published. Source: https://github.com/CUHK-Shenzhen-SE/UTBoost. sha256 `b1ded300f056cbd0363c68c58f55234e211d6ffb8cf5781e78d1a8370e267083`.
