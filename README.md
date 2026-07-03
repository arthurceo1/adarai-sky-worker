# adarai-sky-worker

Images serverless RunPod pour les workflows Sky (Studio NSFW adarai).

- Environnement de nodes FIGE aux commits exacts des pods manuels qui marchent
  (snapshots du 2026-07-03). Pas de mise a jour automatique, jamais.
- ZERO modele dans l'image : les modeles restent sur les network volumes RunPod
  (volume image / volume video), vus via extra_model_paths.yaml.
- Build via GitHub Actions -> ghcr.io/arthurceo1/adarai-sky-image et -sky-video.
- Endpoints: adarai-sky-image (volume fltqcxeib1) / adarai-sky-video (volume l5kebpp3kk).
