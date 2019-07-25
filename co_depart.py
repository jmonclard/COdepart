import os
import sys
import argparse
import json
import random
from operator import itemgetter
import coloredlogs
import logging
import datetime
import csv

__updated__ = '2019-07-25 17:36:01'

# ======================================================
# Qui Quand      Quoi
# PP  25/04/2019 Version initiale
# JM  15/05/2019 Refonte et ajout fonctionnalités
# JM  05/06/2019 Rajout option circuit sur dossards
# JM  11/06/2019 Génération des étiquettes et listes par club pour enveloppes
# JM  13/06/2019 Meilleure gestion du CSV d'inscription
# JM  18/06/2019 Gestion T-shirts
# JM  21/06/2019 Possibilité d'ajouter dans le JSON des identifiants IOF
# JM  24/06/2019 Correction bug en cas d'absence de solution pour un concurrent
# JM  28/06/2019 Correction bug génération étapes en l'absence d'horaires de type auto ou ranking
# JM  30/06/2019 Ajout possibilité d'affecter le numéro de licence FFCO et le pays d'un club dansle JSON
# JM  25/07/2019 Correction problème membres d'un "petit" pays dans un "petit" club si différent du pays du club
# ======================================================
# PP : ppardo@metraware.com
# JM : jmonclard@metraware.com
# ======================================================


DEFAULT_JSON_CONFIG = "ofrance2019.json"
DEFAULT_JSON_CODES_PAYS = "codespays.json"
MAX_TENTATIVES = 100


class LogTool(object):

    def __init__(self, level, usefile=True, logfile="./co_depart.log"):
        self.log = logging.getLogger("CODepartLogger")
        # error correspond ici au niveau minimum (on voit donc toujours les error et critical)
        level = logging.ERROR - 10 * level
        if level < logging.NOTSET:
            level = logging.NOTSET
        # niceformat = '%(asctime)s.%(msecs)03d %(levelname)-8s %(funcName)-20s %(filename)17s %(lineno)5d %(message)s'
        niceformat = '%(levelname)-8s %(message)s'
        # niceformat = '%(message)s'
        coloredlogs.install(fmt=niceformat, datefmt='%H:%M:%S', level=level)
        self.log.debug("Log started")

        if usefile:
            self.log.debug("Enable log file at " + str(logfile))
            fh = logging.FileHandler(str(logfile),
                                     mode='w')  # le fichier de log est append, mais on ne stocke QUE les grosses erreurs
            fh.setLevel(level)  # logging.WARNING
            fmt = logging.Formatter(fmt=niceformat, datefmt='%H:%M:%S')
            fh.setFormatter(fmt)
            self.log.addHandler(fh)

    def getlogger(self):
        return self.log


class CODepart(object):
    """
    Calcul d'horaires de départ pour la CO
    """
    def __init__(self, argv=None):

        if argv is None:
            argv = sys.argv[1:]

        # gestion des arguments de la ligne de commande de cette application
        parser = argparse.ArgumentParser(description="CO Départ",
                                         epilog="(c)CO")
        parser.add_argument('-v',
                            dest="verbose",
                            action="count",
                            default=5,
                            help="Increase verbosity level (can be used many times)")
        parser.add_argument('-c',
                            dest="config",
                            default=DEFAULT_JSON_CONFIG,
                            help="Course (defaut=" + DEFAULT_JSON_CONFIG + ")")
        parser.add_argument('-p',
                            dest="codespays",
                            default=DEFAULT_JSON_CODES_PAYS,
                            help="Fichier des codes pays (defaut=" + DEFAULT_JSON_CODES_PAYS + ")")

        self.args = parser.parse_args(argv)

        self.log = LogTool(self.args.verbose).getlogger()

        if not os.path.exists(self.args.codespays):
            raise Exception("Fichier " + self.args.codespays + " introuvable.")
        self.codespays = self.getCodesPays(self.args.codespays)['CodesPays']

        if not os.path.exists(self.args.config):
            raise Exception("Fichier " + self.args.config + " introuvable.")

        self.epreuve = self.getConfiguration(self.args.config)
        self.epreuve['NbEtapes'] = len(self.epreuve['Etapes'])

        if not os.path.exists(self.epreuve['Inscriptions']['FichierCSV']):
            raise Exception("Dont find file " + self.epreuve['Inscriptions']['FichierCSV'])
        self.competiteurs = []  # pour le contenu total
        # id;prenom;nom;sexe;an;flag;num_club;ref_club;club;num_lic;etapes;circuit;puce_si;remarques
        self.cn = []  # base ffco du cn
        self.iof = {}  # base ranking iof clef=idiof, value=dictionnaire des rankings
        self.clubs = {}  # {Nom_du_club,{Effectif, groupe}}

    def getCodesPays(self, f):
        contenu = None
        with open(f, encoding='utf8') as json_data:
            contenu = json.load(json_data)
        return contenu

    def Code3ToCode2(self, code3):
        f = [v.get('Code2') for v in self.codespays if v['Code3'] == code3]
        if len(f) == 0:
            raise Exception("Code pays " + code3 + " inconnu")
        return f[0]

    # Lecture du fichier JSON de configuration

    def getConfiguration(self, f):
        contenu = None
        with open(f, encoding='utf8') as json_data:
            contenu = json.load(json_data)
        return contenu

    def convertToGoodCategory(self, cat):
        '''
        Remplacement de la catégorie définie dans le fichier d'entrée par la catégorie correspondante définie dans la section AliasCategories.
        '''
        found = [r['output'] for r in self.epreuve['AliasCategories'] if r['input'] == cat]

        if len(found) != 1:
            print(found)
            print(cat)
            print("-------------------------")
            print(self.epreuve['AliasCategories'])
            print("-------------------------")
            print("Catégorie " + str(cat) + " inconnue !")
            raise Exception("Cannot find " + str(cat) + " in AliasCategories.")

        return found[0]

    def getCategoryId(self, cat):
        found = [r['id'] for r in self.epreuve['AliasCategories'] if r['output'] == cat]

        if len(found) < 1:
            raise Exception("Cannot find " + str(cat) + " in AliasCategories.")

        return found[0]

    def circuitDeLaCategorie(self, etape, categorie):
        circuits = self.epreuve['Etapes'][etape]['Circuits']
        circuit = None
        for c in circuits:
            if categorie in c['Categories']:
                circuit = c['Nom']
        return circuit

    def departDeLaCategorie(self, etape, categorie):
        circuits = self.epreuve['Etapes'][etape]['Circuits']
        depart = None
        for c in circuits:
            if categorie in c['Categories']:
                depart = c['Depart']
        return depart

    def horaireDeLaCategorie(self, etape, categorie):
        circuits = self.epreuve['Etapes'][etape]['Circuits']
        horaire = None
        for c in circuits:
            if categorie in c['Categories']:
                horaire = c['Horaires']
        return horaire

    def remplacement5Etapes(self, etapes_str):
        '''
        Remplacement de "5 ETAPES" par "1,2,3,4,5"
        '''
        if "5 ETAPES" in etapes_str:
            etapes_str = "1,2,3,4,5"
        return etapes_str

    def ajoutCompetiteur(self, nom, prenom, sexe, club, numclub, refclub, annee, flag, categorie, licenceffco, puce_si,
                         modeletshirt, tailletshirt, etapes, iofid):
        # Recherche des points IOF
        mespointsiofsprint = 0
        mespointsiofmdld = 0
        mespointsiofmtbo = 0
        mespointsiofskio = 0
        mespointsioftrailo = 0

        if iofid.strip() in self.iof:
            if 'PedestreSprint' in self.iof[iofid]:
                mespointsiofsprint = int(self.iof[iofid]['PedestreSprint'])
            if 'PedestreMDLD' in self.iof[iofid]:
                mespointsiofmdld = int(self.iof[iofid]['PedestreMDLD'])
            if 'MTBO' in self.iof[iofid]:
                mespointsiofmtbo = int(self.iof[iofid]['MTBO'])
            if 'SkiO' in self.iof[iofid]:
                mespointsiofskio = int(self.iof[iofid]['SkiO'])
            if 'TrailO' in self.iof[iofid]:
                mespointsioftrailo = int(self.iof[iofid]['TrailO'])

        # Rechercher des points CN
        mespointscn = 0
        if self.epreuve['RankingFFCO']['UtiliserRankingFFCO'] == 'oui':
            pts = [p['pointscn'] for p in self.cn if p['num_lic'] == licenceffco.strip()]
            if len(pts) == 1:
                mespointscn = int(pts[0])

        macategorie = self.convertToGoodCategory(categorie.strip())
        mesetapes_one_based = list(map(int, self.remplacement5Etapes(etapes).split(",")))
        mesetapes = [x - 1 for x in mesetapes_one_based]

        monsexe = sexe.strip()
        for x in self.epreuve['AliasSexes']:
            if sexe.strip() in x:
                monsexe = x[sexe.strip()].strip()

        monclub = club.strip()
        for x in self.epreuve['AliasClubs']:
            if club.strip() in x:
                monclub = x[club.strip()].strip()

        monprenom = prenom.strip().title()
        for x in self.epreuve['AliasPrenoms']:
            if monprenom in x:
                monprenom = x[monprenom.strip()].title().strip()

        mescircuits = [None] * self.epreuve['NbEtapes']
        mesdeparts = [None] * self.epreuve['NbEtapes']
        meshoraires = [None] * self.epreuve['NbEtapes']
        mestranches = [None] * self.epreuve['NbEtapes']
        for etape in range(self.epreuve['NbEtapes']):
            if etape in mesetapes:
                mescircuits[etape] = self.circuitDeLaCategorie(etape, macategorie)
                mesdeparts[etape] = self.departDeLaCategorie(etape, macategorie)
                meshoraires[etape] = self.horaireDeLaCategorie(etape, macategorie)

        mesheuresdedepart = [None] * self.epreuve['NbEtapes']

        self.competiteurs.append(
            {
                'prenom': monprenom,
                'nom': nom.strip(),
                'sexe': monsexe,
                'an': annee.strip(),
                'flag': flag.strip(),
                'num_club': numclub.strip(),
                'ref_club': refclub.strip(),
                'club': monclub,
                'num_lic': licenceffco.strip(),
                'etapes': mesetapes,
                'categorie': macategorie,
                'puce_si': puce_si.strip(),
                'modele_tshirt': modeletshirt.strip(),
                'taille_tshirt': tailletshirt.strip(),
                'iofid': iofid.strip(),
                'dossard': None,
                'heure_dep': mesheuresdedepart,
                'tranches': mestranches,
                'posdepart': mesdeparts,
                'pointscn': mespointscn,
                'pointsiofsprint': mespointsiofsprint,
                'pointsiofmdld': mespointsiofmdld,
                'pointsiofmtbo': mespointsiofmtbo,
                'pointsiofskio': mespointsiofskio,
                'pointsioftrailo': mespointsioftrailo,
                'circuits': mescircuits,
                'horaires': meshoraires
            }
        )

    def ajoutLicenceFFO(self, nom, prenom, club, licenceffco):
        licenceffco = str(licenceffco)
        ip = None
        for i, m in enumerate(self.competiteurs):
            if (m['nom'] == nom) and (m['prenom'] == prenom) and (m['club'] == club):
                ip = i
        if ip is not None:
            self.competiteurs[ip]['num_lic'] = licenceffco
            # Recherche des points CN
            mespointscn = 0

            pts = [p['pointscn'] for p in self.cn if p['num_lic'] == licenceffco.strip()]
            if len(pts) == 1:
                mespointscn = int(pts[0])

                self.competiteurs[ip]['pointscn'] = mespointscn
        else:
            self.log.warning("Impossible d'affecter le numéro de licence FFCO au compétiteur : " + nom + ' ' + prenom + ' du club : ' + club)

    def ajoutIdIof(self, nom, prenom, club, iofid):
        iofid = str(iofid)
        ip = None
        for i, m in enumerate(self.competiteurs):
            if (m['nom'] == nom) and (m['prenom'] == prenom) and (m['club'] == club):
                ip = i
        if ip is not None:
            self.competiteurs[ip]['iofid'] = iofid
            # Recherche des points IOF
            mespointsiofsprint = 0
            mespointsiofmdld = 0
            mespointsiofmtbo = 0
            mespointsiofskio = 0
            mespointsioftrailo = 0

            if iofid in self.iof:
                if 'PedestreSprint' in self.iof[iofid]:
                    mespointsiofsprint = int(self.iof[iofid]['PedestreSprint'])
                if 'PedestreMDLD' in self.iof[iofid]:
                    mespointsiofmdld = int(self.iof[iofid]['PedestreMDLD'])
                if 'MTBO' in self.iof[iofid]:
                    mespointsiofmtbo = int(self.iof[iofid]['MTBO'])
                if 'SkiO' in self.iof[iofid]:
                    mespointsiofskio = int(self.iof[iofid]['SkiO'])
                if 'TrailO' in self.iof[iofid]:
                    mespointsioftrailo = int(self.iof[iofid]['TrailO'])

                self.competiteurs[ip]['pointsiofsprint'] = mespointsiofsprint
                self.competiteurs[ip]['pointsiofmdld'] = mespointsiofmdld
                self.competiteurs[ip]['pointsiofmtbo'] = mespointsiofmtbo
                self.competiteurs[ip]['pointsiofskio'] = mespointsiofskio
                self.competiteurs[ip]['pointsioftrailo'] = mespointsioftrailo
        else:
            self.log.warning("Impossible d'affecter l'Id IOF au compétiteur : " + nom + ' ' + prenom + ' du club : ' + club)

    def suppressionCompetiteur(self, nom, prenom, club):
        ip = None
        for i, m in enumerate(self.competiteurs):
            if (m['nom'] == nom) and (m['prenom'] == prenom) and (m['club'] == club):
                ip = i
        if ip is not None:
            del(self.competiteurs[ip])
        else:
            self.log.warning('Impossible de supprimer le compétiteur : ' + nom + ' ' + prenom + ' du club : ' + club)

    # Lecture du fichier CSV des inscrits
    def importFromCSVData(self):
        filename = self.epreuve['Inscriptions']['FichierCSV']
        self.log.info('Ouverture de ' + filename)
        lig = 0

        with open(filename, 'r', encoding=self.epreuve['Inscriptions']['Encodage']) as f:
            reader = csv.reader(f, delimiter=self.epreuve['Inscriptions']['SeparateurColonnesCSV'])
            for row in reader:
                lig += 1
                if lig > self.epreuve['Inscriptions']['LignesDeTitre']:  # suppression des lignes de titre
                    nom = row[self.epreuve['Inscriptions']['Colonnes']['Nom'] - 1]
                    prenom = row[self.epreuve['Inscriptions']['Colonnes']['Prenom'] - 1]
                    sexe = row[self.epreuve['Inscriptions']['Colonnes']['Sexe'] - 1]
                    club = row[self.epreuve['Inscriptions']['Colonnes']['NomClub'] - 1]
                    numclub = row[self.epreuve['Inscriptions']['Colonnes']['NumeroClub'] - 1]
                    refclub = row[self.epreuve['Inscriptions']['Colonnes']['ReferenceClub'] - 1]
                    categorie = row[self.epreuve['Inscriptions']['Colonnes']['Categorie'] - 1]
                    annee = row[self.epreuve['Inscriptions']['Colonnes']['Annee'] - 1]
                    flag = row[self.epreuve['Inscriptions']['Colonnes']['Pays'] - 1]
                    licenceffco = row[self.epreuve['Inscriptions']['Colonnes']['NumeroLicenceFFCO'] - 1]
                    puce_si = row[self.epreuve['Inscriptions']['Colonnes']['NumeroPuceSI'] - 1]
                    etapes = row[self.epreuve['Inscriptions']['Colonnes']['Etapes'] - 1]
                    iofid = row[self.epreuve['Inscriptions']['Colonnes']['IOFid'] - 1]
                    modeletshirt = row[self.epreuve['Inscriptions']['Colonnes']['ModeleTshirt'] - 1]
                    tailletshirt = row[self.epreuve['Inscriptions']['Colonnes']['TailleTshirt'] - 1]

                    self.ajoutCompetiteur(nom, prenom, sexe, club, numclub, refclub, annee, flag, categorie, licenceffco, puce_si,
                                          modeletshirt, tailletshirt, etapes, iofid)
            # enfor line
        # with open

        # ---- Ajout des Id IOF
        for p in self.epreuve['RankingIOF']['AjoutIdentifiant']:
            self.ajoutIdIof(p['Nom'].strip(), p['Prenom'].strip(), p['Club'].strip(), p['IdIof'])

        # ---- Ajout des numéros de licence FFCO
        for p in self.epreuve['RankingFFCO']['AjoutIdentifiant']:
            self.ajoutLicenceFFO(p['Nom'].strip(), p['Prenom'].strip(), p['Club'].strip(), p['LicenceFFCO'])

        # ---- Suppression des inscrits à supprimer
        for p in self.epreuve['Inscriptions']['ParticipantsASupprimer']:
            self.suppressionCompetiteur(p['Nom'], p['Prenom'], p['Club'])
            self.log.info('Suppression du compétiteur : ' + p['Nom'] + ' ' + p['Prenom'] + ' du club : ' + p['Club'])

        # ---- Ajout des inscriptions supplémentaires
        for p in self.epreuve['Inscriptions']['ParticipantsSupplementaires']:
            self.ajoutCompetiteur(p['Nom'], p['Prenom'], p['Sexe'], p['NomClub'], p['NumeroClub'], p['ReferenceClub'],
                                  p['Annee'], p['Pays'], p['Categorie'], p['NumeroLicenceFFCO'], p['NumeroPuceSI'],
                                  p['ModeleTshirt'], p['TailleTshirt'], p['Etapes'], p['IOFid'])
            self.log.info('Ajout du compétiteur : ' + p['Nom'] + ' ' + p['Prenom'] + ' du club : ' + p['NomClub'])

    # Lecture du fichier CSV du ranking IOF
    def importFromIofCSV(self):

        for s in ('Men', 'Women'):
            for r in self.epreuve['RankingIOF']['FichierCSV'][s]:
                filename = self.epreuve['RankingIOF']['FichierCSV'][s][r]
                if os.path.isfile(filename):
                    self.log.info('Ouverture de ' + filename)
                    with open(filename, 'r', encoding=self.epreuve['RankingIOF']['Encodage']) as f:
                        for line in f.readlines():
                            if not line.strip().startswith(self.epreuve['RankingIOF']['DebutTitre']):  # suppression de la ligne de titre
                                row = line.strip().split(self.epreuve['RankingIOF']['SeparateurColonnesCSV'])
                                iofid = row[self.epreuve['RankingIOF']['Colonnes']['Id'] - 1].strip()
                                if iofid not in self.iof:
                                    self.iof[iofid] = {}
                                self.iof[iofid][r] = row[self.epreuve['RankingIOF']['Colonnes']['Points'] - 1].strip()

    def importFromFfcoCSV(self):
        '''
        Lecture du fichier CSV du ranking FFCO
        '''
        filename = self.epreuve['RankingFFCO']['FichierCSV']
        if os.path.isfile(filename):
            self.log.info('Ouverture de ' + filename)
            with open(filename, 'r', encoding=self.epreuve['RankingFFCO']['Encodage']) as f:
                for line in f.readlines():
                    if not line.strip().startswith(self.epreuve['RankingFFCO']['DebutTitre']):  # suppression de la ligne de titre
                        row = line.strip().split(self.epreuve['RankingFFCO']['SeparateurColonnesCSV'])
                        self.cn.append(
                            {
                                'prenom': row[self.epreuve['RankingFFCO']['Colonnes']['Prenom'] - 1].strip('" '),
                                'nom': row[self.epreuve['RankingFFCO']['Colonnes']['Nom'] - 1].strip('" '),
                                'ranking': row[self.epreuve['RankingFFCO']['Colonnes']['Ranking'] - 1].strip('" '),
                                'pointscn': row[self.epreuve['RankingFFCO']['Colonnes']['CN'] - 1].strip('" '),
                                'num_lic': row[self.epreuve['RankingFFCO']['Colonnes']['NumeroLicenceFFCO'] - 1].strip('" ')
                            }
                        )

    def dumpInformation(self, data_intr, fileOutputMD, keepVacant=True, extraParcours=None):
        # dump des informations sur des datas
        ic = 0
        for c in sorted(data_intr.items(), key=itemgetter(1), reverse=True):
            doIt = keepVacant
            if not doIt:
                doIt = c[0] != "Vacant" and len(c[0]) > 0
            if doIt:
                if ic == 0:
                    fileOutputMD.write("  -")
                if extraParcours:
                    # cas spécial categorie=>circuit
                    fileOutputMD.write(" **" + str(c[0]).strip() + "** [" + self.findCircuit(c[0], extraParcours) + "] (_" + str(c[1]) + "_),")
                else:
                    # cas général
                    fileOutputMD.write(" **" + str(c[0]).strip() + "** (_" + str(c[1]) + "_),")
                ic += 1
                if ic % 10 == 0:
                    fileOutputMD.write("\n")
                    ic = 0
        if ic != 0:
            fileOutputMD.write("\n")

    def knownCategorie(self, depart, categorie, lst_circuits):
        known = [v for v in lst_circuits if v['Depart'] == depart and categorie in v['Categories']]
        if len(known) > 1:
            self.log.error("[" + str(len(known)) + "] La catégorie " + str(categorie) + " est présente plusieurs fois au départ " +
                           str(depart) + " dans la liste " + str(lst_circuits))
        return known

    def genereListeParClub(self):
        with open(self.epreuve['Enveloppes']['NomFichierLaTeX'] + ".tex", "w", encoding='utf-8') as file:
            file.write("\\documentclass{report}\n")
            file.write("\\usepackage[a4paper, " +
                       " left=" + str(self.epreuve['Enveloppes']['MargeGauche_mm']) + "mm," +
                       " right=" + str(self.epreuve['Enveloppes']['MargeDroite_mm']) + "mm," +
                       " top=" + str(self.epreuve['Enveloppes']['MargeSuperieure_mm']) + "mm," +
                       " bottom=" + str(self.epreuve['Enveloppes']['MargeInferieure_mm']) + "mm," +
                       "]{geometry}\n")
            file.write("\\usepackage{graphicx}\n")
            file.write("\\usepackage{longtable}\n")
            file.write("\\usepackage{lmodern}\n")
            file.write("\\begin{document}\n")
            file.write("  \\setlength\\tabcolsep{2pt}\n")
            file.write("  \\pagenumbering{gobble}\n")
            file.write("  \\sffamily\n")

            for club, v in sorted(self.clubs.items()):
                nomclub = club.replace("\\", "\\\\")
                nomclub = nomclub.replace("&", "\\&")
                flagfilename = self.epreuve['FlagsSubdirectory'] + "/" + self.Code3ToCode2(v['flag']).lower() + ".png"

                file.write("  \\Huge \\centering \\bfseries " + nomclub + " " + str(v['refclub']) + " " + v['flag'] +
                           "\\normalfont \\footnotesize \\sffamily \\hfill \\includegraphics[height=" +
                           str(self.epreuve['Enveloppes']['HauteurDrapeau_mm']) + "mm]{" + flagfilename + "} \\newline \n")

                if self.epreuve['Enveloppes']['CircuitsSurListe'] == 'oui':
                    file.write("  \\begin{longtable}{|c|l|r|c|c|*{" + str(self.epreuve['NbEtapes']) + "}{ccc|}}\n")
                    file.write("    Dossard & Nom  & Puce    & Catégorie & T-shirt & \\multicolumn{" +
                               str(3 * self.epreuve['NbEtapes']) +
                               "}{c|}{Circuits, nom du départ et heures de départ} | \\\\\n")
                    file.write("    \\itshape Bib     & \\itshape  Name & \\itshape SI card & \\itshape  Class  & \\itshape  T-shirt   & \\multicolumn{" +
                               str(3 * self.epreuve['NbEtapes']) +
                               "}{c|}{\\itshape Courses, start names and start times} | \\\\\n")
                    file.write("    \\hline\n")
                    ch = ''
                    for etape in range(self.epreuve['NbEtapes']):
                        ch += " & \\multicolumn{3}{c|}{Étape " + str(etape + 1) + "}"
                    file.write("    & & & &" + ch + " \\\\\n")
                    ch = ''
                    for etape in range(self.epreuve['NbEtapes']):
                        ch += " & \\multicolumn{3}{c|}{\\itshape Stage " + str(etape + 1) + "}"
                    file.write("    & & & &" + ch + " \\\\\n")
                else:
                    file.write("  \\begin{longtable}{|c|l|r|c|c|*{" + str(self.epreuve['NbEtapes']) + "}{cc|}}\n")
                    file.write("    Dossard & Nom  & Puce    & Catégorie & T-shirt & \\multicolumn{" +
                               str(2 * self.epreuve['NbEtapes']) +
                               "}{c|}{Nom du départ et heures de départ} \\\\\n")
                    file.write("    \\itshape Bib     & \\itshape Name & \\itshape SI card & \\itshape Class  & \\itshape  T-shirt  & \\multicolumn{" +
                               str(2 * self.epreuve['NbEtapes']) +
                               "}{c|}{\\itshape Start names and start times} \\\\\n")
                    file.write("    \\hline\n")
                    ch = ''
                    for etape in range(self.epreuve['NbEtapes']):
                        ch += " & \\multicolumn{2}{c|}{Étape " + str(etape + 1) + "}"
                    file.write("    & & & &" + ch + " \\\\\n")
                    ch = ''
                    for etape in range(self.epreuve['NbEtapes']):
                        ch += " & \\multicolumn{2}{c|}{\\itshape Stage " + str(etape + 1) + "}"
                    file.write("    & & & &" + ch + " \\\\\n")
                file.write("    \\hline\n")
                coureurs_du_club = [x for x in self.competiteurs if x['club'] == club]
                for comp in sorted(coureurs_du_club, key=lambda k: k['nom'] + " " + k['prenom']):
                    etapes_str = ""
                    for etape in range(self.epreuve['NbEtapes']):
                        etapes_str += " & "
                        if etape in comp['etapes']:
                            if comp['heure_dep'][etape] is None:
                                h_str = ' '
                            else:
                                h_str = comp['heure_dep'][etape].strftime("%H:%M")
                            if self.epreuve['Enveloppes']['CircuitsSurListe'] == 'oui':
                                etapes_str += str(comp['circuits'][etape]) + " & " + str(comp['posdepart'][etape]) + " & " + h_str
                            else:
                                etapes_str += str(comp['posdepart'][etape]) + " & " + h_str
                        else:
                            if self.epreuve['Enveloppes']['CircuitsSurListe'] == 'oui':
                                etapes_str += "-  & - &  -"
                            else:
                                etapes_str += "- &  -"
                    # endfor
                    if comp['modele_tshirt'] == "":
                        tshirt = " "
                    else:
                        tshirt = comp['modele_tshirt'] + "/" + comp['sexe'] + "/" + comp['taille_tshirt']
                    file.write("    " + str(comp['dossard']) + " & " +
                               comp['nom'] + " " + comp['prenom'] + " & " +
                               str(comp['puce_si']) + " & " +
                               comp['categorie'][:14] + " & " +
                               tshirt +
                               etapes_str + "\\\\\n")
                file.write("  \\end{longtable}\n")
                file.write("\\newpage\n")

            file.write("\\end{document}\n")

    def genereEtiquettes(self):
        with open(self.epreuve['Etiquettes']['NomFichierLaTeX'] + ".tex", "w", encoding='utf-8') as file:
            file.write("\\documentclass{report}\n")
            file.write("\\usepackage[a4paper, left=" + str(self.epreuve['Etiquettes']['MargeGauche_mm']) +
                       "mm, right=0mm, top=" + str(self.epreuve['Etiquettes']['MargeSuperieure_mm']) +
                       "mm, bottom=0mm]{geometry}\n")
            file.write("\\usepackage[most]{tcolorbox}\n")
            file.write("\\usepackage{lmodern}\n")
            file.write("\\begin{document}\n")
            file.write("  \\newcommand{\\txt}[5]{\n")
            file.write("    \\tcboxfit{%\n")
            file.write("        \\sffamily \\Huge \\bfseries ~ #1 ~\\par%\n")
            file.write("        \\large #2\\par%\n")
            file.write("        \\vspace{3mm}%\n")
            file.write("        #3\\hfill \\includegraphics[height=" + str(self.epreuve['Etiquettes']['HauteurDrapeau_mm']) + "mm]{#5}\\hfill #4%\n")
            file.write("      }%\n")
            file.write("    }\n")

            file.write("  \\tcbset{\n")
            file.write("    colframe=" + str(self.epreuve['Etiquettes']['CouleurCadre']) + ",\n")
            file.write("    colback=white,\n")
            file.write("    size=tight,\n")
            file.write("    nobeforeafter,\n")
            file.write("    valign=center,\n")
            file.write("    fit fontsize macros,\n")
            file.write("    fit algorithm=fontsize,\n")
            file.write("    boxsep=" + str(self.epreuve['Etiquettes']['MargeEntreColonnes_mm']) + "mm,\n")
            file.write("    width=" + str(self.epreuve['Etiquettes']['LargeurEtiquette_mm']) + "mm,\n")
            file.write("    height=" + str(self.epreuve['Etiquettes']['HauteurEtiquette_mm']) + "mm,\n")
            file.write("    halign=flush center\n")
            file.write("  }\n")
            file.write("\n")
            file.write("  \\setlength\\parindent{0pt}\n")
            file.write("\n")

            col = 0
            lig = 0
            for c, v in sorted(self.clubs.items()):
                flagfilename = self.epreuve['FlagsSubdirectory'] + "/" + self.Code3ToCode2(v['flag']).lower() + ".png"
                nomclub = c.replace("\\", "\\\\")
                nomclub = nomclub.replace("&", "\\&")
                file.write("  \\txt{" + nomclub + "}{" + str(v['refclub']) + "}{" + str(v['dossardmin']) + "}{" + str(v['dossardultramax']) +
                           "}{" + flagfilename + "}")
                if (lig == self.epreuve['Etiquettes']['NombreDeLignes'] - 1) and (col == self.epreuve['Etiquettes']['NombreDeColonnes'] - 1):
                    file.write("\\newpage\n")
                    col = 0
                    lig = 0
                elif col == self.epreuve['Etiquettes']['NombreDeColonnes'] - 1:
                    file.write("\\vspace{" + str(self.epreuve['Etiquettes']['MargeEntreLignes_mm']) + "mm} \\newline\n")
                    col = 0
                    lig += 1
                else:
                    file.write("\n")
                    col += 1

            file.write("\\end{document}\n")

    def genereFichierDossards(self):
        sep = self.epreuve['Dossards']['SeparateurColonnesCSV']
        with open(self.epreuve['Dossards']['NomFichierCSVDossards'] + ".csv", "w", encoding='ansi') as file:
            if self.epreuve['Dossards']['CircuitsSurDossards'] == 'oui':
                out_str = sep.join(
                    [
                        'Dossard', 'Puce', 'Nom', 'Prenom', 'cat.', 'Nom Club', 'Nat', 'T-shirt',
                        'Circuit1', 'Depart1', 'H Depart1',
                        'Circuit2', 'Depart2', 'H Depart2',
                        'Circuit3', 'Depart3', 'H Depart3',
                        'Circuit4', 'Depart4', 'H Depart4',
                        'Circuit5', 'Depart5', 'H Depart5'
                    ]
                )
            else:
                out_str = sep.join(
                    [
                        'Dossard', 'Puce', 'Nom', 'Prenom', 'cat.', 'Nom Club', 'Nat', 'T-shirt',
                        'Depart1', 'H Depart1',
                        'Depart2', 'H Depart2',
                        'Depart3', 'H Depart3',
                        'Depart4', 'H Depart4',
                        'Depart5', 'H Depart5'
                    ]
                )

            max_dossard = 0

            file.write(out_str + '\n')
            # dossards des inscrits (hors vacants)
            for p in self.competiteurs:
                if p['flag'] != 'Vacant':
                    if p['dossard'] > max_dossard:
                        max_dossard = p['dossard']
                    if p['modele_tshirt'] == "":
                        tshirt = " "
                    else:
                        tshirt = p['modele_tshirt'] + "/" + p['sexe'] + "/" + p['taille_tshirt']

                    out_str = sep.join(
                        [
                            str(p['dossard']), str(p['puce_si']), str(p['nom']), str(p['prenom']), str(p['categorie']), str(p['club']), str(p['flag']), tshirt
                        ]
                    )
                    etapes_str = ''
                    for etape in range(self.epreuve['NbEtapes']):
                        etapes_str += ','
                        if etape in p['etapes']:
                            if p['heure_dep'][etape] is None:
                                h_str = ' '
                            else:
                                h_str = p['heure_dep'][etape].strftime("%H:%M")
                            if self.epreuve['Dossards']['CircuitsSurDossards'] == 'oui':
                                etapes_str += sep.join([str(p['circuits'][etape]), str(p['posdepart'][etape]), h_str])
                            else:
                                etapes_str += sep.join([str(p['posdepart'][etape]), h_str])
                        else:
                            if self.epreuve['Dossards']['CircuitsSurDossards'] == 'oui':
                                etapes_str += sep.join(['**', '*', '*****'])
                            else:
                                etapes_str += sep.join(['*', '*****'])
                    # endfor
                    file.write(out_str + etapes_str + '\n')
                # endif
            # endfor

            # dossards supplemetaires
            for _ in range(int(self.epreuve['Dossards']['DossardsSupplementaires'])):
                max_dossard += 1
                out_str = sep.join([str(max_dossard), ' ', ' ', ' ', ' ', ' ', ' ', ' '])
                etapes_str = ''
                for etape in range(self.epreuve['NbEtapes']):
                    etapes_str += ','
                    if self.epreuve['Dossards']['CircuitsSurDossards'] == 'oui':
                        etapes_str += sep.join([' ', ' ', ' '])
                    else:
                        etapes_str += sep.join([' ', ' '])
                file.write(out_str + etapes_str + '\n')

    def genereCSV(self, etape, contenu):
        out_str = ''
        sep = self.epreuve['FichiersGeneres']['SeparateurColonnesCSV']
        with open(self.epreuve['FichiersGeneres']['NomFichierCSV'] + "_Etape_" + str(etape + 1) + ".csv", "w", encoding='ansi') as file:
            if self.epreuve['FichiersGeneres']['GEC'] == 'MeOS':
                out_str = sep.join(
                    [
                        'Depart', 'Puce', 'Licence', 'Prenom', 'Nom', 'Ne', 'S',
                        'Plage', 'nc', 'H Depart', 'H Arrivée', 'Temps', 'Evaluation',
                        'Num club', 'Id club', 'Nom Club', 'Nat', 'Num cat.', 'Court', 'Long',
                        'Num1', 'Num2', 'Num3', 'Dossards', 'Text2', 'Text3', 'Adr. nom', 'Rue', 'Ligne2', 'Code Post.', 'Ville',
                        'Tel.', 'Fax', 'E-mail', 'Id/Club', 'Louée', 'Engagement', 'Paye'
                    ]
                )
            else:  # format pour d'autres logiciels de GEC à écrire ici
                pass

            file.write(out_str + '\n')
            file.write(contenu)

    def genereLigneCSV(self, p, etape, heure):
        out_str = ''
        sep = self.epreuve['FichiersGeneres']['SeparateurColonnesCSV']
        num_categorie = self.getCategoryId(p['categorie'])
        pts_iof = self.pointsIofDeLEtape(p, etape)
        if self.epreuve['FichiersGeneres']['GEC'] == 'MeOS':
            out_str = sep.join(
                [
                    '', str(p['puce_si']), str(p['num_lic']), str(p['prenom']), str(p['nom']),
                    str(p['an']), str(p['sexe']), '', '0', str(heure),
                    '', '', '0', str(p['num_club']), str(p['ref_club']),
                    str(p['club']), str(p['flag']), str(num_categorie), str(p['categorie']), '',
                    '', '', '', str(p['dossard']),
                    '', '', '', '', '', '', '', '', '', '', '', '', str(pts_iof), str(p['iofid'])
                ]
            )
        else:  # format pour d'autres logiciel de GEC à écrire ici
            pass
        return out_str

    def genereLigneMarkdown(self, fileOutputMD, p, etape):
        fileOutputMD.write('|' + ' | '.join(
            [
                str(p['dossard']),
                '{:<18}'.format((p['nom'] + " " + p['prenom']).strip())[:18],
                (p['ref_club'] + ' ' + p['club']).strip(),
                p['categorie'][:16] + ' (' + p['circuits'][etape] + ')',
                p['posdepart'][etape]
            ]) + '|\n')

    def genereLigneMarkdownAvecPoints(self, fileOutputMD, p, etape):
        mespointsiof = self.pointsIofDeLEtape(p, etape)
        fileOutputMD.write('|' + ' | '.join(
            [
                str(p['dossard']),
                '{:<18}'.format((p['nom'] + " " + p['prenom']).strip())[:18],
                (p['ref_club'] + ' ' + p['club']).strip(),
                p['categorie'][:16] + ' (' + p['circuits'][etape] + ')',
                p['posdepart'][etape], str(mespointsiof), str(p['pointscn'])
            ]) + '|\n')

    def genereLigneMarkdownSansPoints(self, fileOutputMD, p, etape):
        fileOutputMD.write('|' + ' | '.join(
            [
                str(p['dossard']),
                '{:<18}'.format((p['nom'] + " " + p['prenom']).strip())[:18],
                (p['ref_club'] + ' ' + p['club']).strip(),
                p['categorie'][:16] + ' (' + p['circuits'][etape] + ')',
                p['posdepart'][etape], ' ', ' '
            ]) + '|\n')

    def pointsIofDeLEtape(self, p, etape):
        etape_info = self.epreuve['Etapes'][etape]
        mespointsiof = 0
        if etape_info['Format'] == "PedestreSprint":
            mespointsiof = p['pointsiofsprint']
        elif etape_info['Format'] == "PedestreMDLD":
            mespointsiof = p['pointsiofmdld']
        elif etape_info['Format'] == "MTBO":
            mespointsiof = p['pointsiofmtbo']
        elif etape_info['Format'] == "PedestreSkiO":
            mespointsiof = p['pointsiofskio']
        elif etape_info['Format'] == "TrailO":
            mespointsiof = p['pointsioftrailo']
        return mespointsiof

    def traitementEtape(self, etape, fileOutputMD):
        self.log.info("====== Traitement de l'étape " + str(etape + 1) + "======")
        self.calculHeuresMinMaxTranches(etape, fileOutputMD)
        # if self.epreuve['Etapes'][etape]['FinTranches'][0] is not None:
        self.repartitionParticipants(etape, fileOutputMD)

    def calculHeureDebutDuCircuitDansLaTranche(self, heure_debut_circuit, intervalle, heure_debut_tranche):
        heure_debut = None
        if (heure_debut_tranche is not None and heure_debut_circuit is not None):
            minutes_depuis_debut = heure_debut_tranche - heure_debut_circuit
            intervalles_depuis_debut = int((minutes_depuis_debut.total_seconds() / 60.0 + intervalle - 1) / intervalle)
            heure_debut = heure_debut_circuit + datetime.timedelta(minutes=intervalles_depuis_debut * intervalle)
        return heure_debut

    def calculHeureMinFinDuCircuitDansLaTranche(self, heure_debut_circuit, intervalle, heure_debut_tranche, nbparticipants, marge):
        heure_debut = self.calculHeureDebutDuCircuitDansLaTranche(heure_debut_circuit, intervalle, heure_debut_tranche)
        duree_en_minutes = intervalle * nbparticipants + marge
        heure_fin = heure_debut + datetime.timedelta(minutes=duree_en_minutes)
        return heure_fin

    def calculHeuresMinMaxTranches(self, etape, fileOutputMD):
        """ Calcul des heures Min/Max des tranches de l'étape à partir du nombre de participants, de l'heure de
            début des circuits et du nombre de participants du circuit
        """
        self.epreuve['Etapes'][etape]['DebutTranches'] = [None] * self.epreuve['TranchesHoraires']['NbTranches']
        self.epreuve['Etapes'][etape]['FinTranches'] = [None] * self.epreuve['TranchesHoraires']['NbTranches']

        circuits_de_l_etape = self.epreuve['Etapes'][etape]['Circuits']
        circuits_auto_de_l_etape = [c for c in circuits_de_l_etape if c['Horaires'] == 'oui']

        if len(circuits_auto_de_l_etape) > 0:
            heure_debut_tranche = datetime.datetime.strptime(circuits_auto_de_l_etape[0]['HeureDepart'], "%Y/%m/%dT%H:%M")
            for index_circuit in range(len(circuits_auto_de_l_etape)):
                h = datetime.datetime.strptime(circuits_auto_de_l_etape[index_circuit]['HeureDepart'], "%Y/%m/%dT%H:%M")
                circuits_auto_de_l_etape[index_circuit]['premier_depart'] = h
                if h < heure_debut_tranche:
                    heure_debut_tranche = h
                circuits_auto_de_l_etape[index_circuit]['intervalle_departs'] = int(circuits_auto_de_l_etape[index_circuit]['Ecart'])

            for t in range(self.epreuve['TranchesHoraires']['NbTranches']):
                heure_fin_tranche = heure_debut_tranche
                presentAuto_parts = [p for p in self.competiteurs if etape in p['etapes']
                                     and p['circuits'][etape] in [c['Nom'] for c in circuits_auto_de_l_etape]
                                     and p['tranches'][etape] == t]

                stats_circuits = {}         # {nom_circuit, nbre participants pour cette tranche de cette étape}
                for p in presentAuto_parts:
                    if p['circuits'][etape] not in stats_circuits:
                        stats_circuits[p['circuits'][etape]] = 0
                    stats_circuits[p['circuits'][etape]] += 1

                for c in circuits_auto_de_l_etape:
                    if c['Nom'] in stats_circuits:
                        nbre_participants = stats_circuits[c['Nom']]  # nbre participants pour ce circuit à cette tranche de cette étape
                    else:
                        nbre_participants = 0
                    heure_debut_circuit = c['premier_depart']
                    intervalle = c['intervalle_departs']
                    marge = self.epreuve['TranchesHoraires']['MargeTranches']

                    heure_fin = self.calculHeureMinFinDuCircuitDansLaTranche(heure_debut_circuit, intervalle, heure_debut_tranche, nbre_participants, marge)

                    if heure_fin > heure_fin_tranche:
                        heure_fin_tranche = heure_fin
                self.epreuve['Etapes'][etape]['DebutTranches'][t] = heure_debut_tranche
                self.epreuve['Etapes'][etape]['FinTranches'][t] = heure_fin_tranche
                self.log.debug('Etape ' + str(etape + 1) + ' tranche ' + str(t + 1) + ' : début=' + str(heure_debut_tranche) + ' fin=' + str(heure_fin_tranche))
                heure_debut_tranche = heure_fin_tranche + datetime.timedelta(minutes=1)
            # endfor tranche
        # endif non vide

    # ---------------------------------------------------------------------------------------------------------------------------------------------------
    def ajouteStats(self, participants, etape, fileOutputMD, circuits_de_l_etape):
        categories_intr = dict()
        circuits_intr = dict()
        clubs_intr = dict()
        flags_intr = dict()
        for r in participants:
            if r['categorie'] not in categories_intr:
                categories_intr[r['categorie']] = 1
            else:
                categories_intr[r['categorie']] += 1

            if r['circuits'][etape] not in circuits_intr:
                circuits_intr[r['circuits'][etape]] = 1
            else:
                circuits_intr[r['circuits'][etape]] += 1
            if r['club'] not in clubs_intr:
                clubs_intr[r['club']] = 1
            else:
                clubs_intr[r['club']] += 1
            if r['flag'] not in flags_intr:
                flags_intr[r['flag']] = 1
            else:
                flags_intr[r['flag']] += 1

        fileOutputMD.write("- Nombre de coureurs : " + str(len(participants)) + "\n")
        if len(participants) > 0:
            fileOutputMD.write("- Nombre de catégories : " + str(len(categories_intr)) + "\n")
            self.dumpInformation(categories_intr, fileOutputMD, keepVacant=True, extraParcours=circuits_de_l_etape)
            fileOutputMD.write("- Nombre de circuits : " + str(len(circuits_intr)) + "\n")
            self.dumpInformation(circuits_intr, fileOutputMD)
            fileOutputMD.write("- Nombre de clubs : " + str(len(clubs_intr) - 1) + "\n")  # attention -1 car il y a le club Vacant
            self.dumpInformation(clubs_intr, fileOutputMD, keepVacant=True)
            fileOutputMD.write("- Nombre de pays : " + str(len(flags_intr) - 1) + "\n")  # attention -1 car il y a le club Vacant
            self.dumpInformation(flags_intr, fileOutputMD, keepVacant=True)
            fileOutputMD.write("\n")

    # ---------
    def affectationSuivantRanking(self, etape, participants, circuits_ranking, depart_all):

        # Liste des circuits avec ranking
        for circ in circuits_ranking:
            heure_courante = datetime.datetime.strptime(circ['HeureDepart'], "%Y/%m/%dT%H:%M")
            ecart = int(circ['Ecart'])

            # En premier ceux qui n'ont ni ranking iof ni cn
            presents_sans_ranking_ni_cn = [i for (i, p) in enumerate(participants) if
                                           p['circuits'][etape] == circ['Nom'] and p['pointscn'] == 0
                                           and self.pointsIofDeLEtape(p, etape) == 0]
            random.shuffle(presents_sans_ranking_ni_cn)  # ordre aléatoire si pas de ranking

            for ip in presents_sans_ranking_ni_cn:
                participants[ip]['heure_dep'][etape] = heure_courante
                participants[ip]['posdepart'][etape] = circ['Depart']
                if heure_courante not in depart_all:
                    depart_all[heure_courante] = []
                depart_all[heure_courante].append(participants[ip])
                heure_courante += datetime.timedelta(minutes=ecart)

            # ensuite ceux qui ont un cn mais pas de ranking iof
            presents_sans_ranking_avec_cn = [(i, p['pointscn']) for (i, p) in enumerate(participants) if
                                             p['circuits'][etape] == circ['Nom'] and p['pointscn'] > 0 and
                                             self.pointsIofDeLEtape(p, etape) == 0]
            tries = sorted(presents_sans_ranking_avec_cn, key=lambda k: k[1])
            for ip in tries:
                participants[ip[0]]['heure_dep'][etape] = heure_courante
                participants[ip[0]]['posdepart'][etape] = circ['Depart']
                if heure_courante not in depart_all:
                    depart_all[heure_courante] = []
                depart_all[heure_courante].append(participants[ip[0]])
                heure_courante += datetime.timedelta(minutes=ecart)

            # enfin ceux qui ont un ranking iof
            presents_avec_ranking = [(i, self.pointsIofDeLEtape(p, etape)) for (i, p) in enumerate(participants) if
                                     p['circuits'][etape] == circ['Nom'] and
                                     self.pointsIofDeLEtape(p, etape) > 0]
            tries = sorted(presents_avec_ranking, key=lambda k: k[1])
            for ip in tries:
                participants[ip[0]]['heure_dep'][etape] = heure_courante
                participants[ip[0]]['posdepart'][etape] = circ['Depart']
                if heure_courante not in depart_all:
                    depart_all[heure_courante] = []
                depart_all[heure_courante].append(participants[ip[0]])
                heure_courante += datetime.timedelta(minutes=ecart)
        # endfor circuit

    def genereFichiersSortie(self, fileOutputMD, etape, depart_all, zeroDate,
                             noms_circuits_ranking, liste_participants_manuel, liste_participants_depart_boitier, liste_participants_libre):

        self.log.debug("remplissage md et CSV")
        # remplissage md et CSV
        if len(depart_all) > 0:
            fileOutputMD.write("\n### Horaires des départs\n\n")
            fileOutputMD.write("- Heure zéro GEC : " + str(zeroDate) + "\n\n")

        csv_out_str = ""
        for p in sorted(depart_all.keys()):
            deps = depart_all[p]
            fileOutputMD.write('\n- **' + str(p) + '** (+' + str(p - zeroDate) + ')\n\n')
            fileOutputMD.write('| Dossard | Nom | Club | Cat. | Départ | IOF | CN |\n')
            fileOutputMD.write('| - | - | - | - | - | - | - |\n')

            # 'Depart','Puce' ,'N° licence','Prénom','Nom'       ,'Né','S','Plage','nc','Départ','Arrivée','Temps','Evaluation','N° club','Id club','Nom Club',
            # 'Nat' ,'N° cat.','Court','Long','Num1','Num2','Num3','Dossards','Text2','Text3','Adr. nom','Rue','Ligne2','Code Post.','Ville','Tél.','Fax',
            # 'E-mail','Id/Club','Louée','Engagement','Payé'
            #        ,1130311,29334       ,Patrick ,AGOSTINELLI ,1961 ,M ,       ,0   ,09:00:00,         ,       ,0           ,1303     ,1303PZ   ,1303PZ MARCO,
            # France,1        ,H55    ,      ,      ,      ,      ,100       ,,,,,,,,,,,,,,
            for copart in sorted(deps, key=lambda k: k['categorie']):
                out_str = self.genereLigneCSV(copart, etape, heure=str(copart['heure_dep'][etape] - zeroDate))
                csv_out_str += out_str + '\n'
                circ = self.circuitDeLaCategorie(etape, copart['categorie'])
                if circ in noms_circuits_ranking:
                    self.genereLigneMarkdownAvecPoints(fileOutputMD, copart, etape)
                else:
                    self.genereLigneMarkdownSansPoints(fileOutputMD, copart, etape)

        if len(liste_participants_manuel) > 0:
            fileOutputMD.write('\n### Traitement manuel nécessaire\n\n')
            fileOutputMD.write('| Dossard | Nom | Club | Cat. | Départ |\n')
            fileOutputMD.write('| - | - | - | - | - |\n')
            for mp in liste_participants_manuel:
                for p in mp:
                    out_str = self.genereLigneCSV(p, etape, heure='')
                    csv_out_str += out_str + '\n'
                    self.genereLigneMarkdown(fileOutputMD, p, etape)

        if len(liste_participants_depart_boitier) > 0:
            fileOutputMD.write('\n### Départs au boîtier\n\n')
            fileOutputMD.write('| Dossard | Nom | Club | Cat. | Départ |\n')
            fileOutputMD.write('| - | - | - | - | - |\n')
            for mp in liste_participants_depart_boitier:
                for p in mp:
                    out_str = self.genereLigneCSV(p, etape, heure='')
                    csv_out_str += out_str + '\n'
                    self.genereLigneMarkdown(fileOutputMD, p, etape)

        if len(liste_participants_libre) > 0:
            fileOutputMD.write('\n### Participants non chronométrés\n\n')
            fileOutputMD.write('| Dossard | Nom | Club | Circuit | Départ |\n')
            fileOutputMD.write('| - | - | - | - | - |\n')
            for mp in liste_participants_libre:
                for p in mp:
                    self.genereLigneMarkdown(fileOutputMD, p, etape)

        self.genereCSV(etape, csv_out_str)

    def gestionParticipantsOublies(self, fileOutputMD, participants, etape):
        # recherche des participants oubliés
        lst_oublies = [p for p in participants if (not p['heure_dep'][etape] and p['horaires'][etape] in ['oui', 'rank'])]

        if len(lst_oublies) > 0:
            fileOutputMD.write('\n### Erreurs détectées\n\n')
            fileOutputMD.write('#### Causes problables des erreurs\n\n')
            fileOutputMD.write("- Un circuit/catégorie n'est pas présent dans un des départs, dans ce cas là il est impossible de l'affecter à un départ.")
            fileOutputMD.write(" Il faut vérifier dans la section `Depart-Circuits`.\n")
            fileOutputMD.write("- Un parcours n'est pas prévu pour un circuit/catégorie. Il faut vérifier dans la section `Parcours-Circuits`.\n")
            fileOutputMD.write('\n')

            # les catégories en erreur
            lst_categ_err = {cat['categorie'] for cat in lst_oublies}
            if len(lst_categ_err) > 0:
                self.log.critical("Etape " + str(1 + etape) + ", " + str(len(lst_oublies)) +
                                  " erreur(s) détectée(s) pour les catégories : " + str(lst_categ_err))
                fileOutputMD.write('\n#### Catégories en erreur\n\n')
                for c in lst_categ_err:
                    fileOutputMD.write('- ' + str(c) + "\n")
                fileOutputMD.write('\n')

            # les circuits en erreur
            lst_circuit_err = {circ['circuits'][etape] for circ in lst_oublies}
            if len(lst_circuit_err) > 0:
                self.log.critical("Etape " + str(1 + etape) + ", " + str(len(lst_oublies)) + " Erreur(s) détectée(s) pour les circuits " + str(lst_circuit_err))
                fileOutputMD.write('\n#### Circuits en erreur\n\n')
                for c in lst_circuit_err:
                    fileOutputMD.write('- ' + str(c) + "\n")
                fileOutputMD.write('\n')

            # les participants qui ont soufferts des erreurs
            fileOutputMD.write('\n#### Participants non traités du fait des erreurs\n\n')
            fileOutputMD.write('| Dossard | Licence | Nom | Club | Cat. | Départ |\n')
            fileOutputMD.write('| - | - | - | - | - | - |\n')
            for copart in lst_oublies:
                fileOutputMD.write('|' + ' | '.join(
                    [
                        str(copart['dossard']),
                        str(copart['num_lic']),
                        (copart['nom'] + " " + copart['prenom']).strip(),
                        (copart['ref_club'] + ' ' + copart['club']).strip(),
                        str(copart['categorie']) + ' (' + copart['circuits'][etape] + ')',
                        str(copart['posdepart'][etape])
                    ]) + '|\n')
            exit(1)

    # ==========================================================================================================================================
    def etalementDeparts(self, bins, bincounts, circuits_de_l_etape):
        self.log.debug("Traitement de l'étalement")
        # traitement de la répartition
        #
        # Algorithme au pif, mais un gros dans un petit
        previousEc = max(bincounts) - min(bincounts) + 1
        countLoop = 0

        for _ in range(500):
            bincounts = [len(bins[bin]) for bin in bins]
            minbincount = min(bincounts)
            maxbincount = max(bincounts)
            currentEc = maxbincount - minbincount

            if previousEc == currentEc:
                countLoop += 1
            else:
                countLoop = 0
            detectLoop = countLoop > 50
            if currentEc < 2 or detectLoop:
                break
            previousEc = currentEc

            # liste des horaires ayant le plus grand nombre de départs simultanés
            maxSbincount = [bin for bin in bins if len(bins[bin]) == int(maxbincount)]

            replaced = False
            for heure_du_max in maxSbincount:
                # un element dans le max
                # trions par ordre de ceux qui ont le plus de place dispo
                table_data = dict()
                for index_element in range(len(bins[heure_du_max])):
                    index_circuit = self.getIndexDuCircuitDeLaCategorie(circuits_de_l_etape, bins[heure_du_max][index_element]['categorie'])
                    table_data[index_element] = len(circuits_de_l_etape[index_circuit]['HeuresDispo'])
                index_table_data_sorted = sorted(table_data, key=table_data.__getitem__, reverse=True)  # donne les index

                data_dump = ''
                for index_element in index_table_data_sorted:
                    data_dump += str(table_data[index_element]) + ", "

                for index_a_replacer in index_table_data_sorted:
                    element_a_replacer = bins[heure_du_max][index_a_replacer]

                    index_circuit = self.getIndexDuCircuitDeLaCategorie(circuits_de_l_etape, element_a_replacer['categorie'])
                    nb_poss = len(circuits_de_l_etape[index_circuit]['HeuresDispo'])
                    if nb_poss > 0:
                        start_index = random.randint(0, nb_poss)
                        for _ in range(nb_poss):  # on teste toutes les heures !
                            index_tentative = start_index % nb_poss
                            circuit = circuits_de_l_etape[index_circuit]
                            heure_tentative = circuit['HeuresDispo'][index_tentative]

                            if heure_tentative not in bins.keys() or (heure_tentative in bins.keys() and len(bins[heure_tentative]) < int(maxbincount) - 1):
                                if self.horaireAcceptable(bins, heure_tentative, circuit, element_a_replacer, circuits_de_l_etape):
                                    if heure_tentative not in bins.keys():
                                        bins[heure_tentative] = []
                                    bins[heure_tentative].append(element_a_replacer)
                                    bins[heure_du_max].pop(index_a_replacer)
                                    circuit['HeuresDispo'].pop(index_tentative)
                                    circuits_de_l_etape[index_circuit]['HeuresDispo'].append(heure_du_max)
                                    replaced = True
                                    break
                            start_index += 1
                    if replaced:
                        break
                if replaced:
                    break

    def genereInfoEtalementCategories(self, fileOutputMD, etape, bins, categories_intr, circuits_de_l_etape, intervalles):

        fileOutputMD.write("\n#### Etalement des catégories\n\n")
        fileOutputMD.write('| Cat. | Circuit | 1er dép. | dern. dép. | Nb | Interv. |\n')
        fileOutputMD.write('| - | - | - | - | - | - |\n')

        hminmin = None
        hmaxmax = None
        for categ in sorted(categories_intr.items()):
            # recherche du circuit
            index_circuit = self.getIndexDuCircuitDeLaCategorie(circuits_de_l_etape, categ[0])
            hmin = None
            hmax = None
            for bin in bins:
                for part in bins[bin]:
                    if part['categorie'] == categ[0]:
                        h = part['heure_dep'][etape]
                        if hmin is None:
                            hmin = h
                        elif h < hmin:
                            hmin = h
                        if hminmin is None:
                            hminmin = h
                        elif h < hminmin:
                            hminmin = h

                        if hmax is None:
                            hmax = h
                        elif h > hmax:
                            hmax = h
                        if hmaxmax is None:
                            hmaxmax = h
                        elif h > hmaxmax:
                            hmaxmax = h

            if hmin is None:
                hmin_str = ''
            else:
                hmin_str = hmin.strftime("%H:%M:%S")

            if hmax is None:
                hmax_str = ''
            else:
                hmax_str = hmax.strftime("%H:%M:%S")

            fileOutputMD.write('|' + ' | '.join(
                [
                    categ[0],
                    circuits_de_l_etape[index_circuit]['Nom'],
                    hmin_str,
                    hmax_str,
                    str(categ[1]),
                    str(intervalles[index_circuit]),
                ]) + "|\n")
        fileOutputMD.write("\n")
        return (hminmin, hmaxmax)

    def genereInfoEtalementCircuits(self, fileOutputMD, etape, bins, presentAuto_parts, circuits_de_l_etape, intervalles):
        fileOutputMD.write("\n#### Etalement des circuits\n\n")
        fileOutputMD.write('| Circuit | 1er dép. | dern. dép. | Nb | Interv. |\n')
        fileOutputMD.write('| - | - | - | - | - |\n')

        circuits_intr = dict()
        for r in presentAuto_parts:
            if r['circuits'][etape] not in circuits_intr:
                circuits_intr[r['circuits'][etape]] = 1
            else:
                circuits_intr[r['circuits'][etape]] += 1

        for circuit in sorted(circuits_intr.items()):
            index_circuit = self.getIndexDuCircuit(circuits_de_l_etape, circuit[0])
            hmin = None
            hmax = None
            for bin in bins:
                for part in bins[bin]:
                    if part['circuits'][etape] == circuit[0]:
                        h = part['heure_dep'][etape]
                        if hmin is None:
                            hmin = h
                        elif h < hmin:
                            hmin = h

                        if hmax is None:
                            hmax = h
                        elif h > hmax:
                            hmax = h

            if hmin is None:
                hmin_str = ''
            else:
                hmin_str = hmin.strftime("%H:%M:%S")

            if hmax is None:
                hmax_str = ''
            else:
                hmax_str = hmax.strftime("%H:%M:%S")

            fileOutputMD.write('|' + ' | '.join(
                [
                    circuits_de_l_etape[index_circuit]['Nom'],
                    hmin_str,
                    hmax_str,
                    str(circuits_de_l_etape[index_circuit]['NbParticipants']),
                    str(intervalles[index_circuit]),
                ]) + "|\n")

    # ==========================================================================================================================================

    def repartitionParticipants(self, etape, fileOutputMD):
        """ Répartition des participants
        - par départs, catégories, circuits, ...
        """
        participants = [p for p in self.competiteurs if etape in p['etapes']]

        self.log.debug("Répartition participants pour l'étape " + str(etape + 1))
        self.log.debug("Nombre de participants " + str(len(participants)))

        zeroDate_str = self.epreuve['Etapes'][etape]['ZeroDate']
        zeroDate = datetime.datetime.strptime(zeroDate_str, "%Y/%m/%dT%H:%M")

        fileOutputMD.write("---\n\n")
        fileOutputMD.write("## Etape " + self.epreuve['Etapes'][etape]['Nom'] + "\n\n")
        fileOutputMD.write("- Lieu : **" + self.epreuve['Etapes'][etape]['Lieu'] + "**\n")
        fileOutputMD.write("- Info : " + self.epreuve['Etapes'][etape]['Information'] + "\n")
        fileOutputMD.write("- zeroDate : " + str(zeroDate) + "\n")
        fileOutputMD.write("\n")  # fin de liste

        circuits_de_l_etape = self.epreuve['Etapes'][etape]['Circuits']

        heures_debut_circuits = len(circuits_de_l_etape) * [None]
        intervalles = len(circuits_de_l_etape) * [None]
        depart_all = dict()

        liste_participants_manuel = []
        liste_participants_libre = []
        liste_participants_depart_boitier = []

        local_info_departs = {d['Depart'] for d in circuits_de_l_etape}
        for depart in sorted(local_info_departs):
            fileOutputMD.write("\n---\n\n### Départ " + str(depart) + "\n\n")
            self.log.debug("------ Départ " + str(depart) + "------")

            liste_circuits_manuels = []
            liste_circuits_ranking = []
            liste_circuits_depart_boitier = []
            liste_circuits_auto = []
            liste_circuits_libres = []

            # init des informations temporelles
            for index_circuit in range(len(circuits_de_l_etape)):
                horairesAuto = circuits_de_l_etape[index_circuit]['Horaires']
                # normalement oui, non, manuel, ranking ou boitier
                if horairesAuto == "oui":  # traitement automatique
                    circuits_de_l_etape[index_circuit]['NbParticipants'] = 0
                    liste_circuits_auto.append(circuits_de_l_etape[index_circuit])
                    depart_str = circuits_de_l_etape[index_circuit]['HeureDepart']
                    heures_debut_circuits[index_circuit] = datetime.datetime.strptime(depart_str, "%Y/%m/%dT%H:%M")
                    intervalles[index_circuit] = int(circuits_de_l_etape[index_circuit]['Ecart'])
                elif horairesAuto == "non":  # pas de traitement (non chronométrés)
                    liste_circuits_libres.append(circuits_de_l_etape[index_circuit])
                elif horairesAuto == "boi":  # pas de traitement mais ils sont dans le csv (départ au boîtier)
                    liste_circuits_depart_boitier.append(circuits_de_l_etape[index_circuit])
                elif horairesAuto == "man":  # traitement manuel  (les heures seront affectées à la main)
                    liste_circuits_manuels.append(circuits_de_l_etape[index_circuit])
                elif horairesAuto == "rank":  # départs en fonction du ranking iof puis cn
                    liste_circuits_ranking.append(circuits_de_l_etape[index_circuit])

            for t in range(self.epreuve['TranchesHoraires']['NbTranches']):
                self.log.debug("TRANCHE " + str(t + 1))
                fileOutputMD.write("\n#### Tranche " + str(1 + t) + "\n\n")

                presentAuto_parts = [p for p in participants if
                                     p['tranches'][etape] == t and
                                     len(self.knownCategorie(depart, p['categorie'], liste_circuits_auto)) == 1]
                presentLibre_parts = [p for p in participants if
                                      p['tranches'][etape] == t and
                                      len(self.knownCategorie(depart, p['categorie'], liste_circuits_libres)) == 1]
                presentManuel_parts = [p for p in participants if
                                       p['tranches'][etape] == t and
                                       len(self.knownCategorie(depart, p['categorie'], liste_circuits_manuels)) == 1]
                # pas de tranche pour les catégories au ranking, on les génère lors de l'analyse de la première tranche
                presentRanking_parts = [p for p in participants if
                                        t == 0 and
                                        len(self.knownCategorie(depart, p['categorie'], liste_circuits_ranking)) == 1]
                presentBoitier_parts = [p for p in participants if
                                        p['tranches'][etape] == t and
                                        len(self.knownCategorie(depart, p['categorie'], liste_circuits_depart_boitier)) == 1]

                self.log.debug(
                    "Traitement de :\n" +
                    " auto=" + str(len(presentAuto_parts)) + '\n' +
                    " libre=" + str(len(presentLibre_parts)) + '\n' +
                    " boitier=" + str(len(presentBoitier_parts)) + '\n' +
                    " ranking=" + str(len(presentRanking_parts)) + '\n' +
                    " manuel=" + str(len(presentManuel_parts)) + '\n' +
                    "participants dans la tranche " + str(t + 1) + " de l'étape " + str(etape + 1)
                )

                self.ajouteStats(presentAuto_parts, etape, fileOutputMD, circuits_de_l_etape)

                categories_intr = dict()
                for r in presentAuto_parts:
                    if r['categorie'] not in categories_intr:
                        categories_intr[r['categorie']] = 1
                    else:
                        categories_intr[r['categorie']] += 1

                for categ in categories_intr.items():
                    index = self.getIndexDuCircuitDeLaCategorie(circuits_de_l_etape, categ[0])
                    circuits_de_l_etape[index]['NbParticipants'] = categ[1]

                # Création de bins communs, ensuite on sortira les cas spéciaux
                tentative = 0
                bins_are_good = False
                while (tentative < MAX_TENTATIVES) and (not bins_are_good):
                    tentative += 1
                    self.log.debug("Tentative " + str(tentative) + "... ")

                    # Détermination des horaires disponibles
                    for index_circuit in range(len(circuits_de_l_etape)):
                        dep = self.calculHeureDebutDuCircuitDansLaTranche(
                            heures_debut_circuits[index_circuit],
                            intervalles[index_circuit],
                            self.epreuve['Etapes'][etape]['DebutTranches'][t]
                        )
                        circuits_de_l_etape[index_circuit]['HeuresDispo'] = []
                        if (dep is not None and self.epreuve['Etapes'][etape]['FinTranches'][t] is not None):
                            while dep <= self.epreuve['Etapes'][etape]['FinTranches'][t]:
                                circuits_de_l_etape[index_circuit]['HeuresDispo'].append(dep)
                                dep += datetime.timedelta(minutes=intervalles[index_circuit])

                    bins = dict()

                    categories_by_req = sorted(categories_intr.items(), key=itemgetter(1), reverse=True)
                    bins_are_good = True  # normalement on trouve
                    for c in categories_by_req:
                        # parcourons tous les circuits (du plus chargé au moins chargé) avec répartition aléatoire des heures à affecter
                        for p in presentAuto_parts:
                            if p['categorie'] == c[0]:
                                # cherchons un index de départ
                                foundPlace = False

                                index_circuit = self.getIndexDuCircuitDeLaCategorie(circuits_de_l_etape, p['categorie'])
                                circuit = circuits_de_l_etape[index_circuit]
                                nb_poss = len(circuit['HeuresDispo'])
                                start_index = random.randint(0, nb_poss)
                                for _ in range(nb_poss):  # on teste toutes les heures dispo !
                                    index_tentative = start_index % nb_poss
                                    heure_tentative = circuit['HeuresDispo'][index_tentative]

                                    if self.horaireAcceptable(bins, heure_tentative, circuit, p, circuits_de_l_etape):
                                        if heure_tentative not in bins.keys():
                                            bins[heure_tentative] = []
                                        bins[heure_tentative].append(p)
                                        circuit['HeuresDispo'].pop(index_tentative)
                                        foundPlace = True
                                        break
                                    start_index += 1

                                if not foundPlace:
                                    self.log.debug("(Répartition) Impossible de trouver une place dans le circuit" + str(c) + " pour " + str(p))
                                    bins_are_good = False
                if tentative >= MAX_TENTATIVES:
                    self.log.error("(Répartition) Impossible de trouver une place pour un concurent !")
                else:
                    self.log.debug("Tous les concurrents ont un horaire pour cette tranche.")

                bincounts = [len(bins[bin]) for bin in bins]
                if len(bincounts) > 0:

                    self.log.debug("Répartition par heure de départ (avant étalement) " + str(bincounts) +
                                   ", Ecart max=" + str(max(bincounts) - min(bincounts)))
                    self.etalementDeparts(bins, bincounts, circuits_de_l_etape)
                    bincounts = [len(bins[bin]) for bin in bins]
                    self.log.debug("Répartition par heure de départ (apres traitement) " + str(bincounts) +
                                   ", Ecart max=" + str(max(bincounts) - min(bincounts)))

                    fileOutputMD.write("\n#### Charge de l'atelier départ\n\n")
                    out_str = ''
                    for b in sorted(bins):
                        out_str += str(len(bins[b])) + ', '
                    fileOutputMD.write("- " + str(out_str) + "\n")

                    fileOutputMD.write("- Départs simultanés min : " + str(min(bincounts)) + "\n")
                    fileOutputMD.write("- Départs simultanés max : " + str(max(bincounts)) + "\n")
                    fileOutputMD.write("\n")

                    for bin in bins:
                        for part in bins[bin]:
                            part['heure_dep'][etape] = bin
                            part['posdepart'][etape] = depart

                            if bin not in depart_all:
                                depart_all[bin] = []
                            depart_all[bin].append(part)

                    (hmin, hmax) = self.genereInfoEtalementCategories(fileOutputMD, etape, bins, categories_intr, circuits_de_l_etape, intervalles)
                    self.genereInfoEtalementCircuits(fileOutputMD, etape, bins, presentAuto_parts, circuits_de_l_etape, intervalles)
                    if hmin is None:
                        hmin_str = ''
                    else:
                        hmin_str = hmin.strftime("%H:%M:%S")

                    if hmax is None:
                        hmax_str = ''
                    else:
                        hmax_str = hmax.strftime("%H:%M:%S")

                    fileOutputMD.write('- Premiers départs de la tranche horaire ' + str(t + 1) + ' : ' + hmin_str + '\n')
                    fileOutputMD.write('- Derniers départs de la tranche horaire ' + str(t + 1) + ' : ' + hmax_str + '\n')

                # affectation des départs participants manuels
                if len(presentManuel_parts) > 0:
                    for part in presentManuel_parts:
                        part['posdepart'][etape] = depart
                    liste_participants_manuel.append(presentManuel_parts)
                # affectation des départs des participants non gérés
                if len(presentLibre_parts) > 0:
                    for part in presentLibre_parts:
                        part['posdepart'][etape] = depart
                    liste_participants_libre.append(presentLibre_parts)
                # affectation des départs des participants au boîtier
                if len(presentBoitier_parts) > 0:
                    for part in presentBoitier_parts:
                        part['posdepart'][etape] = depart
                    liste_participants_depart_boitier.append(presentBoitier_parts)
            # endfor tranche
        # endfor depart

        # ------------------------------------------------------------------------------------------------------------------

        self.log.debug("Génération des départs suivant le ranking")
        circuits_ranking = [c for c in circuits_de_l_etape if c['Horaires'] == 'rank']
        self.affectationSuivantRanking(etape, participants, circuits_ranking, depart_all)

        self.log.debug("Génération des fichiers de sortie")
        noms_circuits_ranking = [c['Nom'] for c in circuits_ranking]
        self.genereFichiersSortie(fileOutputMD, etape, depart_all, zeroDate,
                                  noms_circuits_ranking, liste_participants_manuel, liste_participants_depart_boitier, liste_participants_libre)

        self.log.debug("Génération du rapport d'erreurs")
        self.gestionParticipantsOublies(fileOutputMD, participants, etape)

        fileOutputMD.write("\n")
        self.log.info("Fin de traitement de l'étape " + str(etape + 1))

    # end repartitionParticipants

    # ==========================================================================================================================================

    def getIndexDuCircuitDeLaCategorie(self, circuits, categorie):
        index_found = None
        index_cnt = 0
        for p in circuits:
            for c in p['Categories']:
                if categorie == c:
                    index_found = index_cnt
                    break
            index_cnt += 1
        if index_found is None:
            raise Exception("Erreur avec la catégorie " + str(categorie) + " dans la liste " + str([str(ll['Categories']) for ll in circuits]))
        return index_found

    def getIndexDuCircuit(self, circuits, circuit):
        index_found = None
        index_cnt = 0
        for p in circuits:
            if p['Nom'] == circuit:
                index_found = index_cnt
                break
            index_cnt += 1
        if index_found is None:
            raise Exception("Erreur avec le circuit " + str(circuit) + " dans la liste " + str(circuits))
        return index_found

    # Retourne le circuit d'une catégorie parmis les circuits fournis
    def findCircuit(self, categorie, circuits):
        circuit = None
        for circ in circuits:
            for categ in circ['Categories']:
                if categorie == categ:
                    circuit = circ['Nom']
                    break

        return circuit

    # Retourne le groupe horaire d'un club s'il est forcé, sinon retourne -1
    def groupeHoraireForceDuClub(self, club):
        groupe = -1
        for x in self.epreuve['ClubGroupeHoraireForce']:
            if club in x:
                groupe = int(x[club]) - 1
        return groupe

    # Recherche si les circuits associés aux deux catégories sont identiques
    def memeCircuit(self, categ1, categ2, circuits):
        if categ1 == categ2:
            isSame = True
        else:
            # ok elles sont différentes, mais peuvent éventuellement faire le même circuit
            circ1 = self.findCircuit(categ1, circuits)
            circ2 = self.findCircuit(categ2, circuits)
            isSame = circ1 == circ2
        return isSame

    # Recherche si les clubs des deux personnes sont identiques
    def memeClub(self, pers1, pers2):
        nom_club1 = pers1['club']
        ref_club1 = pers1['ref_club']
        num_club1 = pers1['num_club']

        nom_club2 = pers2['club']
        ref_club2 = pers2['ref_club']
        num_club2 = pers2['num_club']

        isSame = (
            ((len(nom_club1) > 0) and (nom_club1 == nom_club2)) or
            ((len(ref_club1) > 0) and (ref_club1 == ref_club2)) or
            ((len(num_club1) > 0) and (num_club1 == num_club2))
        )
        return isSame

    # Vérifie qu'il n'y a personne avec le même circuit à cet horaire
    def aucunSurMemeCircuit(self, bins, heure, personne, circuits=None):
        isOk = True
        if heure in bins.keys():
            for pers in bins[heure]:
                if self.memeCircuit(pers['categorie'], personne['categorie'], circuits):
                    isOk = False
                    break
        return isOk

    # Vérifie qu'il n'y a pas une personne du même club sur le même circuit à l'heure spécifiée
    def aucunMemeClubMemeCircuit(self, bins, heure, personne, circuits=None):
        isOk = True
        if heure in bins.keys():
            for pers in bins[heure]:
                if self.memeCircuit(pers['categorie'], personne['categorie'], circuits) and self.memeClub(pers, personne):
                    isOk = False
                    break
        return isOk

    # Vérifie si cet horaire est acceptable pour cette personne en vérifiant :
    # - qu'il y a personne sur le même circuit
    # - qu'il n'y a personne du même club sur le même circuit avant et après
    def horaireAcceptable(self, bins, heure, circuit, element, circuits):
        heure_avant = heure - datetime.timedelta(minutes=int(circuit['Ecart']))
        heure_apres = heure + datetime.timedelta(minutes=int(circuit['Ecart']))
        isOk = self.aucunSurMemeCircuit(bins, heure, element, circuits) and \
            self.aucunMemeClubMemeCircuit(bins, heure_avant, element, circuits) and \
            self.aucunMemeClubMemeCircuit(bins, heure_apres, element, circuits)
        return isOk

    # Affectation des groupes horaires aux clubs et des tranches horaires aux participants

    def affectationTranches(self, fileOutputMD):
        flags = dict()
        effectifGroupes = self.epreuve['TranchesHoraires']['NbTranches'] * [0]

        for r in self.competiteurs:
            nomclub = r['club']
            if nomclub not in self.clubs:
                pays = r['flag']

                # Recherche si le club a un code de pays forcé
                for c in self.epreuve['NationaliteClubs']:
                    if nomclub in c:
                        pays = c[nomclub.strip()].strip()

                # Ajout du club à la liste des clubs
                self.clubs[nomclub] = {'nomcomplet': nomclub + ' ' + str(r['ref_club']),
                                       'refclub': str(r['ref_club']),
                                       'effectif': None,
                                       'groupe': None,
                                       'flag': pays,
                                       'dossardmin': None,
                                       'dossardmax': None,
                                       'dossardultramax': None}
                self.clubs[nomclub]['effectif'] = 1
            else:
                self.clubs[nomclub]['effectif'] += 1

            if r['flag'] not in flags:
                flags[r['flag']] = 1
            else:
                flags[r['flag']] += 1

        # affectation des clubs ayant un groupe forcé
        for c in self.clubs:
            groupe = self.groupeHoraireForceDuClub(c)
            if groupe >= 0:
                self.clubs[c]['groupe'] = groupe
                av = effectifGroupes[groupe]
                # affectation
                for m in self.competiteurs:
                    if m['tranches'][0] is None and m['club'] == c:
                        for i in range(self.epreuve['NbEtapes']):
                            m['tranches'][i] = (i + groupe) % self.epreuve['TranchesHoraires']['NbTranches']
                        effectifGroupes[groupe] += 1
                ap = effectifGroupes[groupe]
                if av != ap:
                    self.log.debug("CLUB " + str(c) + " dans le groupe " + str(groupe + 1) + "  ... " + str(av) + "=>" + str(ap))

        # affectation des gros clubs
        for c, v in self.clubs.items():
            if v['effectif'] >= int(self.epreuve['TranchesHoraires']['SeuilClub']):
                # c'est un grand club, trouvons le groupe ayant l'effectif minimal
                groupe = effectifGroupes.index(min(effectifGroupes))
                v['groupe'] = groupe
                av = effectifGroupes[groupe]
                # affectation
                for m in self.competiteurs:
                    if m['tranches'][0] is None and m['club'] == c:
                        for i in range(self.epreuve['NbEtapes']):
                            m['tranches'][i] = (i + groupe - 1) % self.epreuve['TranchesHoraires']['NbTranches']
                        effectifGroupes[groupe] += 1
                ap = effectifGroupes[groupe]
                if av != ap:
                    self.log.debug("CLUB " + str(c) + " dans le groupe " + str(groupe + 1) + "  ... " + str(av) + "=>" + str(ap))

        # affectation dans un même groupe horaire des concurrents des clubs des pays peu représentés, quel que soit leur club
        for pays in sorted(flags.items(), key=lambda k: k[1], reverse=True):
            if pays[1] <= int(self.epreuve['TranchesHoraires']['SeuilPays']):
                groupe = effectifGroupes.index(min(effectifGroupes))
                av = effectifGroupes[groupe]
                for c, v in self.clubs.items():
                    if v['flag'] == pays[0]:
                        # affectation
                        for m in self.competiteurs:
                            if m['tranches'][0] is None and m['club'] == c:
                                for i in range(self.epreuve['NbEtapes']):
                                    m['tranches'][i] = (i + groupe - 1) % self.epreuve['TranchesHoraires']['NbTranches']
                                effectifGroupes[groupe] += 1
                                self.clubs[m['club']]['groupe'] = groupe
                                # dic_groupes[m['club']] = groupe
                ap = effectifGroupes[groupe]
                if av != ap:
                    self.log.debug("PAYS " + str(pays[0]) + " dans le groupe " + str(groupe + 1) + "  ... " + str(av) + "=>" + str(ap))

        # affectation des membres des petits clubs
        for c in self.clubs:
            # Recherche du groupe ayant l'effectif minimal
            groupe = effectifGroupes.index(min(effectifGroupes))
            av = effectifGroupes[groupe]
            # affectation
            for m in self.competiteurs:
                if m['tranches'][0] is None and m['club'] == c:
                    for i in range(self.epreuve['NbEtapes']):
                        m['tranches'][i] = (i + groupe - 1) % self.epreuve['TranchesHoraires']['NbTranches']
                        self.clubs[c]['groupe'] = groupe
                    effectifGroupes[groupe] += 1
            ap = effectifGroupes[groupe]
            if av != ap:
                self.log.debug("CLUB " + str(c) + " dans le groupe " + str(groupe + 1) + "  ... " + str(av) + "=>" + str(ap))

        # affectation de ce qui reste la ou on trouve (dans le min)
        for m in self.competiteurs:
            if m['tranches'][0] is None:
                texte_err = "Compétiteur n'ayant pas de groupe hoeraire affecté : " + str(m)
                self.log.error(texte_err)
                groupe = effectifGroupes.index(min(effectifGroupes))
                for i in range(self.epreuve['NbEtapes']):
                    m['tranches'][i] = (i + groupe - 1) % self.epreuve['TranchesHoraires']['NbTranches']
                effectifGroupes[groupe] += 1
                self.clubs[m['club']]['groupe'] = groupe

        # Participant à tranche horaire correspondant à un autre club
        # {"nom" :"CARLE", "prenom":"Odin", "club":"ADOCHENOVE", "autreclub":"OE42", "etapes" : [1,2,3,4,5]}
        for p in self.epreuve['ParticipantsTranchesHorairesAutreClub']:
            # affectation
            ip = None
            trouve = False
            for i, m in enumerate(self.competiteurs):
                if m['nom'] == p['nom'] and m['prenom'] == p['prenom'] and m['club'] == p['club']:
                    trouve = True
                    ip = i
#                    if p['autreclub'] in dic_groupes:
#                        grp = dic_groupes[p['autreclub']]
                    if p['autreclub'] in self.clubs:
                        grp = self.clubs[p['autreclub']]['groupe']
                        for i in range(self.epreuve['NbEtapes']):
                            if i + 1 in p['etapes']:
                                self.competiteurs[ip]['tranches'][i] = (i + grp) % self.epreuve['TranchesHoraires']['NbTranches']
                                self.log.info('Affectation de la tranche ' + str(self.competiteurs[ip]['tranches'][i] + 1) + ' à ' +
                                              self.competiteurs[ip]['nom'] + ' ' + self.competiteurs[ip]['prenom'] +
                                              ' du club ' + self.competiteurs[ip]['club'] +
                                              ' correspondant au groupe ' + str(grp + 1) + ' du club ' + p['autreclub'])
                    else:
                        self.log.warning('Impossible de trouver le club : ' + p['autreclub'] + " en vue de l'affectation du groupe horaire à " +
                                         p['nom'] + ' ' + p['prenom'] + ' du club : ' + p['club'])
            if not trouve:
                self.log.warning('Impossible de trouver le compétiteur : ' + p['nom'] + ' ' + p['prenom'] + ' du club : ' + p['club'] +
                                 " en vue de lui affecter les tranches horaires du club " + p['autreclub'])

        # Participants à tranches horaires imposées
        # {"nom" :"BACONNET", "prenom":"ALEXANDRE", "club":"CS PERTUIS", "tranches" : [1,0,1,1,1]}
        for p in self.epreuve['ParticipantsTranchesHorairesForcees']:
            # affectation
            trouve = False
            for m in self.competiteurs:
                if m['nom'] == p['nom'] and m['prenom'] == p['prenom'] and m['club'] == p['club']:
                    trouve = True
                    for i in range(self.epreuve['NbEtapes']):
                        if p['tranches'][i] > 0:
                            m['tranches'][i] = p['tranches'][i] - 1
            if not trouve:
                self.log.warning('Impossible de trouver le compétiteur : ' + p['nom'] + ' ' + p['prenom'] +
                                 ' du club : ' + p['club'] +
                                 " en vue de lui forcer une tranche horaire.")

        # Participant à tranche horaire identique à un autre participant
        # {"nom1" :"ELIAS", "prenom1":" JEAN CLAUDE", "club1":"ACA Aix-en-Provence", "nom2" :"DODIN", "prenom2":"GENEVIEVE", "club2":"HVO", "etapes":[1,2]}
        for pp in self.epreuve['ParticipantsTranchesHorairesIdentiques']:
            # affectation
            ip1 = None
            ip2 = None
            for i, m in enumerate(self.competiteurs):
                if m['nom'] == pp['nom1'] and m['prenom'] == pp['prenom1'] and m['club'] == pp['club1']:
                    ip1 = i
                if m['nom'] == pp['nom2'] and m['prenom'] == pp['prenom2'] and m['club'] == pp['club2']:
                    ip2 = i
            if ip1 is not None and ip2 is not None:
                for i in range(self.epreuve['NbEtapes']):
                    if i + 1 in pp['etapes']:
                        self.competiteurs[ip1]['tranches'][i] = self.competiteurs[ip2]['tranches'][i]
            elif ip1 is None:
                self.log.warning('Impossible de trouver le compétiteur : ' + pp['nom1'] + ' ' + pp['prenom1'] + ' du club : ' + pp['club1'] +
                                 " en vue de l'affectation de la tranche horaire de " + pp['nom2'] + ' ' + pp['prenom2'] + ' du club : ' + pp['club2'])
            else:
                self.log.warning('Impossible de trouver le compétiteur : ' + pp['nom2'] + ' ' + pp['prenom2'] + ' du club : ' + pp['club2'] +
                                 " en vue d'affecter sa tranche horaire à " + pp['nom1'] + ' ' + pp['prenom1'] + ' du club : ' + pp['club1'])

        # On force la tranche horaire des ranking à la première tranche
        # afin d'éviter la multiplication des vacants pour ces catégories
        for i, p in enumerate(self.competiteurs):
            for j, h in enumerate(p['horaires']):
                if h == 'rank':
                    self.competiteurs[i]['tranches'][j] = 0

        self.log.info("Effectif des groupes de tranches horaires:" + str(effectifGroupes))
        fileOutputMD.write('\n')
        fileOutputMD.write('Effectif des groupes de tranches horaires:' + str(effectifGroupes) + '\n')
        fileOutputMD.write('\n')

    # Ajout des vacants à une catégorie d'une tranche d'une étape en fonction du nombre de participants de cette catégorie

    def ajoutVacants(self, etape, categorie, tranche, nbre_presents):
        if nbre_presents > 0:
            nbvacant = int(self.epreuve['TranchesHoraires']['ReserveVacantsOffset']) +\
                int(nbre_presents * int(self.epreuve['TranchesHoraires']['ReserveVacantsPourcent']) / 100)
            for n in range(nbvacant):
                lescircuits = [None] * self.epreuve['NbEtapes']
                lescircuits[etape] = self.circuitDeLaCategorie(etape, categorie)
                lesdeparts = [None] * self.epreuve['NbEtapes']
                lesdeparts[etape] = self.departDeLaCategorie(etape, categorie)
                leshoraires = [None] * self.epreuve['NbEtapes']
                leshoraires[etape] = self.horaireDeLaCategorie(etape, categorie)
                lestranches = [None] * self.epreuve['NbEtapes']
                lestranches[etape] = tranche
                lesetapes = [etape]
                lesheuresdedepart = [None] * self.epreuve['NbEtapes']
                self.competiteurs.append(
                    {
                        'prenom': 'e' + str(etape + 1) + 'c' + categorie + 't' + str(tranche + 1) + 'n' + str(n + 1),
                        'nom': 'Vacant',
                        'sexe': '',
                        'an': '',
                        'flag': 'Vacant',  # ATTENTION NE PAS CHANGER Vacant pour le Flag
                        'num_club': 'Vacant',
                        'ref_club': 'Vacant',
                        'club': 'Vacant',
                        'num_lic': '',
                        'etapes': lesetapes,
                        'categorie': categorie,
                        'puce_si': '0',
                        'iofid': '',
                        'dossard': '',
                        'heure_dep': lesheuresdedepart,
                        'tranches': lestranches,
                        'posdepart': lesdeparts,
                        'pointscn': 0,
                        'pointsiofsprint': 0,
                        'pointsiofmdld': 0,
                        'pointsiofmtbo': 0,
                        'pointsiofskio': 0,
                        'pointsioftrailo': 0,
                        'circuits': lescircuits,
                        'horaires': leshoraires
                    }
                )

    # Ajout des vacants dans les différentes tranches
    def ajoutTousVacants(self, fileOutputMD):
        for etape in range(self.epreuve['NbEtapes']):
            # Liste des catégories de l'étape devant être gérées par la GEC
            participants_par_categorie_de_l_etape = {}  # pour chaque catégorie le nombre par tranche
            for m in self.competiteurs:
                if etape in m['etapes']:
                    circuit = self.circuitDeLaCategorie(etape, m['categorie'])
                    c = [d for d in self.epreuve['Etapes'][etape]['Circuits'] if d['Nom'] == circuit]
                    if (c[0]['Horaires'] != 'non'):
                        if m['categorie'] not in participants_par_categorie_de_l_etape:
                            participants_par_categorie_de_l_etape[m['categorie']] = [0] * self.epreuve['TranchesHoraires']['NbTranches']
                        participants_par_categorie_de_l_etape[m['categorie']][m['tranches'][etape]] += 1

            for categ in participants_par_categorie_de_l_etape:
                # nbre n de participants de cette categorie dans la tranche
                for tranche, nbre_presents in enumerate(participants_par_categorie_de_l_etape[categ]):
                    self.ajoutVacants(etape, categ, tranche, nbre_presents)

    # Affectation des dossards

    def affectationDossards(self, fileOutputMD):

        chrono = int(self.epreuve['Dossards']['PremierDossard'])

        fileOutputMD.write('\n## Affectation des dossards\n\n')
        fileOutputMD.write('| Dossards | Club | Pays | Gr. |\n')
        fileOutputMD.write('| - | - | - | - |\n')

        # dossards par club
        cbyclub = sorted(self.competiteurs, key=lambda k: k['club'])

        in_club = 0
        prev_club = ''

        for r in cbyclub:
            if self.clubs[r['club']]['dossardmin'] is None:
                # On débute un nouveau club
                if in_club > 0:
                    self.clubs[prev_club]['dossardmax'] = self.clubs[prev_club]['dossardmin'] + in_club - 1
                    self.clubs[prev_club]['dossardultramax'] = ((chrono + 12) // 10) * 10 - 1
                    fileOutputMD.write(' | ' + str(self.clubs[prev_club]['dossardmin']) + ' ... ' + str(self.clubs[prev_club]['dossardmax']) +
                                       ' (' + str(self.clubs[prev_club]['effectif']) + ') | ' + self.clubs[prev_club]['nomcomplet'] + ' | ' +
                                       self.clubs[prev_club]['flag'] + ' | ' + str(self.clubs[prev_club]['groupe'] + 1) + '|\n')
                    chrono = ((chrono + 12) // 10) * 10
                prev_club = r['club']
                in_club = 0
                self.clubs[r['club']]['dossardmin'] = chrono

            r['dossard'] = chrono
            chrono += 1
            in_club += 1

        if in_club > 0:
            self.clubs[prev_club]['dossardmax'] = self.clubs[prev_club]['dossardmin'] + in_club - 1
            self.clubs[prev_club]['dossardultramax'] = ((chrono + 12) // 10) * 10 - 1
            fileOutputMD.write(' | ' + str(self.clubs[prev_club]['dossardmin']) + ' ... ' + str(self.clubs[prev_club]['dossardmax']) +
                               ' (' + str(self.clubs[prev_club]['effectif']) + ') | ' + self.clubs[prev_club]['nomcomplet'] + ' | ' +
                               self.clubs[prev_club]['flag'] + ' | ' + str(self.clubs[prev_club]['groupe'] + 1) + '|\n')

        fileOutputMD.write('\n')

    def dataCrunch(self):
        """ Le traitement total des données
        """
        self.log.info("Traitement de " + str(len(self.competiteurs)) + " enregistrements")

        if self.epreuve['GraineGenerateurAleatoire'] == "None":
            random.seed(None)
        else:
            random.seed(int(self.epreuve['GraineGenerateurAleatoire']))

        # Regroupement et traitement
        with open(self.epreuve['FichiersGeneres']['NomFichierMD'] + ".md", "w", encoding='utf8') as fileOutputMD:

            fileOutputMD.write("---\n")
            fileOutputMD.write("title: " + str(self.epreuve['Nom']) + "\n")
            fileOutputMD.write("geometry:\n")  # sinon il faut ajouter geometry dans l'appel à pandoc (au choix)
            fileOutputMD.write("- margin=1.5cm\n")
            fileOutputMD.write("---\n\n")

            # pandoc -V geometry:margin=1.5cm -t latex -f markdown -o ofrance-out.pdf ofrance-out.md

            fileOutputMD.write("## Horaires des départs de " + str(self.epreuve['Nom']) + "\n\n")
            fileOutputMD.write("Généré le " + str(datetime.datetime.now()) + "\n\n")
            fileOutputMD.write("_" + str(self.epreuve['Information']) + "_\n\n")

            self.affectationTranches(fileOutputMD)
            self.affectationDossards(fileOutputMD)
            self.ajoutTousVacants(fileOutputMD)

            for etape in range(self.epreuve['NbEtapes']):
                self.traitementEtape(etape, fileOutputMD)

    def run(self):
        # try:
        self.log.info('co_depart dernier enregiostrement : ' + __updated__)
        self.importFromIofCSV()
        self.importFromFfcoCSV()
        self.importFromCSVData()
        self.dataCrunch()
        self.genereFichierDossards()
        self.genereListeParClub()
        self.genereEtiquettes()
        self.log.info('Fin.')
        # except Exception as e:
        #    self.log.critical(str(e))


"""
Lancement de ce programme
"""

if __name__ == "__main__":
    app = CODepart(sys.argv[1:])
    app.run()
