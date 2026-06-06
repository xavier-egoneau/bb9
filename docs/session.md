# Session

## Intention

Définir ce qu'est une session d'interaction entre un utilisateur, un canal et le système agentique.

Une session porte le contexte court : messages récents, état de travail, tâche en cours, observations utiles et résultat final. Elle complète la mémoire durable sans s'y substituer.

## Contrat

La session doit :

- identifier un échange ou une tâche en cours ;
- conserver le contexte court nécessaire à la continuité ;
- distinguer données temporaires et mémoire durable ;
- pouvoir être résumée, archivée ou oubliée ;
- rester reliée au canal d'origine et aux actions exécutées.

La session ne doit pas :

- devenir la mémoire long terme ;
- devenir un index de contexte ;
- stocker des secrets bruts ;
- dépendre uniquement d'un historique de chat opaque ;
- être mélangée à la configuration globale.

## Questions à résoudre

- Une session appartient-elle au gateway, au channel, ou à la loop ?
- Quelle est sa durée de vie ?
- Quel identifiant minimal utiliser ?
- Que garde-t-on dans une session active ?
- Quand résumer ou archiver une session ?
- Comment relier session, trace, mémoire et actions gateway ?

## Subagents et sessions

Un subagent peut recevoir une session dérivée ou un extrait de session, mais il ne doit pas posséder la session principale.

La loop principale reste responsable de réintégrer le résultat dans le contexte court.

## Position provisoire

La session semble liée au gateway parce qu'elle arrive souvent via un canal concret, mais conceptuellement elle est plutôt entre channel et loop : le channel apporte l'entrée, la session maintient le contexte court, la loop l'utilise, et le gateway trace les effets de bord.

Le contrat détaillé de la trace vit dans `docs/trace.md`.

Le contrat détaillé de la memory vit dans `docs/memory.md`.

## Implémentation initiale

La première session runtime garde un historique court de messages :

- rôle (`user`, `assistant` ou `observation`) ;
- contenu textuel ;
- date ISO.

Cet historique est borné et reste en mémoire dans la session CLI courante.
Il est injecté dans le prompt provider comme contexte récent.

## Persistance

La session courte reste portée en mémoire par la CLI ou le channel actif, mais
elle est aussi archivée dans un store SQLite local :

```text
~/.bb9/sessions.db
```

Ce store garde :

- l'identifiant de session ;
- la source (`cli`, channel futur, cron, etc.) ;
- le projet associé quand il existe ;
- les messages récents ;
- le résumé de compaction ;
- les dates de création, mise à jour et archivage.

Cette persistance n'est pas une mémoire durable. Elle sert à reprendre le
contexte, auditer une interaction récente et donner au dreaming une matière
temporaire à consolider. Le dreaming peut lire les sessions récentes, mais il ne
doit promouvoir en mémoire SQL graph que des faits durables, sourcés et utiles.

La persistance est volontairement un état runtime, pas un Markdown édité par le
système. Les Markdown décrivent les politiques et les contrats ; SQLite porte
l'historique vivant.

Opérations minimales :

- stocker la session courante après un tour ;
- remplacer l'image persistée d'une session quand elle est compactée ;
- archiver une session sans la transformer en mémoire ;
- oublier une session.

Les secrets probables sont masqués avant écriture. Cette protection complète
l'interception locale des secrets, mais ne remplace pas la règle de base :
aucune valeur secrète ne doit être envoyée dans la conversation.

## Historique Visible

La session courte n'est pas l'historique visible complet. BB9 garde donc une
persistance separee pour ce que l'utilisateur peut relire :

```text
~/.bb9/visible-history.db
```

La session sert au contexte actif et a la compaction. L'historique visible sert
aux surfaces, aux notifications, aux futurs rapports et a la reprise humaine.
Un tour peut donc etre ecrit dans les deux stores sans que leurs roles se
melangent.

Les messages visibles de rôle `process` décrivent la progression pour l'UX. Ils
peuvent être persistés dans l'historique visible, mais ne sont pas réinjectés
automatiquement au provider.

Le contrat detaille vit dans `docs/history.md`.

## Compaction

La session peut contenir un résumé dérivé de compaction, séparé de la mémoire durable.

La compaction :

- résume les anciens messages du contexte court ;
- conserve les messages récents ;
- injecte le résumé avant la session récente dans le prompt provider ;
- ne modifie pas `MEMORY.md` ;
- ne stocke pas de secrets volontairement ;
- produit un signal visible quand elle se déclenche automatiquement.

Deux déclenchements existent :

- automatique, quand la session courte devient trop longue, atteint environ 80% de la fenêtre du modèle actif, ou atteint une limite souple d'entrée ;
- manuel, avec la commande REPL `/compact`.

Une auto-compaction ne doit pas être silencieuse. Le channel doit afficher ou
persister une notification courte indiquant combien d'anciens messages ont été
résumés et combien de messages récents restent dans le contexte court. Cette
notification appartient à l'historique visible, mais elle ne doit pas être
réinjectée comme contexte provider.

La fenêtre du modèle est résolue automatiquement depuis un cache local, une table connue embarquée, puis un fallback prudent. La compaction actuelle est déterministe et locale : elle produit un résumé extractif court sans appeler le provider ni le web. Une version LLM plus fine ou une mise a jour web explicite pourront être ajoutées plus tard si le besoin dépasse cette première forme.

Ce n'est pas une mémoire durable :

- `/new` crée une nouvelle session vide ;
- `/compact` réduit le contexte court mais ne crée pas une nouvelle session ;
- rien n'est écrit dans `MEMORY.md` automatiquement ;
- aucun secret ne doit être stocké dans la session.
