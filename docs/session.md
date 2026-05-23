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

## Compaction

La session peut contenir un résumé dérivé de compaction, séparé de la mémoire durable.

La compaction :

- résume les anciens messages du contexte court ;
- conserve les messages récents ;
- injecte le résumé avant la session récente dans le prompt provider ;
- ne modifie pas `MEMORY.md` ;
- ne stocke pas de secrets volontairement.

Deux déclenchements existent :

- automatique, quand la session courte devient trop longue, atteint environ 80% de la fenêtre du modèle actif, ou atteint une limite souple d'entrée ;
- manuel, avec la commande REPL `/compact`.

La fenêtre du modèle est résolue automatiquement depuis un cache local, une table connue embarquée, puis un fallback prudent. La compaction actuelle est déterministe et locale : elle produit un résumé extractif court sans appeler le provider ni le web. Une version LLM plus fine ou une mise a jour web explicite pourront être ajoutées plus tard si le besoin dépasse cette première forme.

Ce n'est pas une mémoire durable :

- `/new` crée une nouvelle session vide ;
- `/compact` réduit le contexte court mais ne crée pas une nouvelle session ;
- rien n'est écrit dans `MEMORY.md` automatiquement ;
- aucun secret ne doit être stocké dans la session.
