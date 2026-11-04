# Publier une version

Versions [SemVer](https://semver.org/lang/fr/) : `0.x.y` tant que l'intégration évolue par
phases (v1 socle = 0.1, v2 courbe = 0.2…) ; correctif seul = `y + 1`.

1. **Merge request** qui porte le changement et monte `version` dans
   `custom_components/de_dietrich_smart/manifest.json`. Le tag ne se pose jamais sur une
   branche de travail.
2. **Après la fusion**, tag annoté sur `main`, posé par Claude Code ou l'administrateur :

   ```bash
   git fetch origin main
   git tag -a vX.Y.Z -m "vX.Y.Z : <résumé>" origin/main
   git push origin vX.Y.Z
   ```

3. **Miroir** : GitLab pousse `main` et le tag vers `github.com/Zanuuu/ha-de-dietrich-smart`.
4. **Release GitHub** (`.github/workflows/release.yml`) : vérifie que le tag égale la version
   du manifeste, construit `de_dietrich_smart.zip` et publie la release. Le Marketplace de
   Home Assistant (HACS avant HA 2026.11) propose alors la mise à jour.
5. **Contrôle** : la release existe sur GitHub avec le zip, et l'action « Validate » est verte.

Un tag publié n'est jamais déplacé ni supprimé : une erreur se corrige par une nouvelle version.

## Prérequis

- Dépôt GitHub **public** avec `hacs.json` : le Marketplace, qui remplace HACS depuis HA 2026.11,
  garde ce fichier et lit toujours `zip_release` et `filename`. Il ne lit que des données publiques,
  mais il lui faut une connexion GitHub pour ajouter un dépôt personnalisé et le tenir à jour.
- Miroir GitLab → GitHub configuré avec un jeton GitHub (*Contents* et *Workflows* en écriture),
  qui transmet aussi les tags.
