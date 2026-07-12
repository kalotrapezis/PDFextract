#!/usr/bin/env python3
"""
Προφίλ χρήσης (use-case profiles) για το knowledge base.

ΑΡΧΗ: **μία βάση ανά χρήση** (ώστε ο agent να ξέρει «τι μπαίνει σε εργασία και τι
όχι»). Κάθε profile ορίζει:
  • db        — προεπιλεγμένο αρχείο βάσης (μέσα στο databases/)
  • summary   — μία γραμμή τι κάνει
  • fields    — η ΛΙΣΤΑ μεταδεδομένων (η «γενική κατεύθυνση»/bullet list). Είναι
                ΣΤΑΘΕΡΗ ανά χρήση ώστε η βάση να μένει queryable. Ο agent τη γεμίζει
                ΑΥΤΟΜΑΤΑ από το περιεχόμενο (με human-in-the-loop έλεγχο).
  • sections  — κανονικές ενότητες που μας ενδιαφέρουν (προαιρετικό· καθοδηγεί
                chunking/ανάγνωση).

ΓΙΑΤΙ έτσι (αυτόματο + bullet list μαζί): σταθερό σχήμα = η βάση φιλτράρεται
αξιόπιστα· αυτόματη συμπλήρωση = δεν πληκτρολογείς μεταδεδομένα ανά έγγραφο.
Τα `fields` χρησιμεύουν 2 φορές: (1) ως οδηγίες εξαγωγής για τον agent,
(2) ως οι στήλες/κλειδιά στο JSON `papers.meta`.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_DIR = ROOT / "databases"


def db_path(key: str) -> Path:
    return DB_DIR / f"{key}.db"


# Σειρά εμφάνισης στο UI.
ORDER = ["research", "thesis", "lesson", "assessment", "personal", "notes", "general"]

PROFILES: dict[str, dict] = {
    "research": {
        "label": "Έρευνα (Research)",
        "summary": "Επιστημονικά paper για ανασκόπηση βιβλιογραφίας & σύνθεση ευρημάτων.",
        "fields": [
            ("authors", "Συγγραφείς (π.χ. Bègue, L., & Gentile, D. A.)"),
            ("year", "Έτος δημοσίευσης"),
            ("title", "Τίτλος"),
            ("journal", "Περιοδικό / εκδότης"),
            ("doi", "DOI"),
            ("study_type", "Τύπος: empirical / review / meta-analysis"),
            ("keywords", "Λέξεις-κλειδιά"),
            ("key_findings", "Βασικά ευρήματα (2-3 γραμμές)"),
        ],
        "sections": ["Abstract", "Introduction", "Methodology", "Results",
                     "Discussion", "Conclusions", "Limitations"],
    },
    "thesis": {
        "label": "Συγγραφή Εργασίας (Thesis)",
        "summary": "Ίδια paper με την Έρευνα, εστιασμένα στη συγγραφή πτυχιακής/εργασίας (academic-db skill).",
        "fields": [
            ("authors", "Συγγραφείς"),
            ("year", "Έτος"),
            ("doi", "DOI"),
            ("apa", "Πλήρης αναφορά APA 7"),
            ("research_question", "Σε ποιο ερευνητικό ερώτημα της εργασίας συνδέεται"),
            ("thesis_section", "Σε ποιο κεφάλαιο της εργασίας ταιριάζει (Εισαγωγή/Μέθοδος/…)"),
            ("stance", "Θέση: υπέρ / κατά / ουδέτερο ως προς το επιχείρημα"),
        ],
        "sections": ["Abstract", "Introduction", "Methodology", "Results",
                     "Discussion", "Conclusions", "Limitations"],
    },
    "lesson": {
        "label": "Σχεδιασμός Μαθήματος (Lesson Planning)",
        "summary": "Σχολικό υλικό & βιβλία ανά τάξη/ενότητα για σχέδια μαθήματος.",
        "fields": [
            ("subject", "Μάθημα (π.χ. ΤΠΕ/Πληροφορική)"),
            ("grade", "Τάξη (π.χ. Ε΄ Δημοτικού)"),
            ("unit", "Ενότητα / κεφάλαιο"),
            ("prev_unit", "Προηγούμενη ενότητα (προαπαιτούμενα)"),
            ("next_unit", "Επόμενη ενότητα (πού οδηγεί)"),
            ("objectives", "Μαθησιακοί στόχοι"),
            ("methodology", "Διδακτική μέθοδος (π.χ. διερευνητική, ομαδοσυνεργατική)"),
            ("tools", "Εργαλεία / ΤΠΕ (π.χ. Scratch, εννοιολογικοί χάρτες)"),
            ("techniques", "Τεχνικές διδασκαλίας"),
            ("duration", "Διάρκεια / διδακτικές ώρες"),
            ("edition", "Έκδοση βιβλίου / έτος"),
        ],
        "sections": ["Στόχοι", "Έννοιες", "Δραστηριότητες", "Αξιολόγηση"],
    },
    "assessment": {
        "label": "Αξιολόγηση & Διαγωνίσματα (Assessment)",
        "summary": "Παραγωγή ασκήσεων/quiz/διαγωνισμάτων & κριτηρίων από το υλικό.",
        "fields": [
            ("subject", "Μάθημα"),
            ("grade", "Τάξη"),
            ("unit", "Ενότητα που αξιολογείται"),
            ("objective", "Μαθησιακός στόχος που ελέγχεται"),
            ("question_type", "Τύπος: πολλαπλής επιλογής / σωστό-λάθος / ανάπτυξη / αντιστοίχιση"),
            ("difficulty", "Δυσκολία / επίπεδο Bloom (ανάκληση…αξιολόγηση)"),
            ("answer_key", "Σωστή απάντηση / ενδεικτική λύση"),
        ],
        "sections": [],
    },
    "personal": {
        "label": "Προσωποποίηση (Personalization)",
        "summary": "Προσωπικές πληροφορίες ώστε ο agent να προσαρμόζει τις απαντήσεις σε σένα.",
        "fields": [
            ("category", "Κατηγορία: προσωπικά / οικογένεια / εργασία / οικονομικά / υγεία / διακοπές / διατροφή / προτιμήσεις"),
            ("people", "Πρόσωπα που αφορά"),
            ("date", "Ημερομηνία / περίοδος"),
            ("location", "Τόπος"),
            ("detail", "Η πληροφορία (σύντομα)"),
            ("sensitivity", "Ευαισθησία: low / medium / high"),
        ],
        "sections": [],
    },
    "notes": {
        "label": "Σημειώσεις / Knowledge Base (Notes)",
        "summary": "Προσωπικές σημειώσεις & ιδέες — «δεύτερος εγκέφαλος» με συνδέσεις (Zettelkasten).",
        "fields": [
            ("topic", "Θέμα"),
            ("source", "Πηγή (βιβλίο/άρθρο/δικό μου)"),
            ("tags", "Ετικέτες"),
            ("links", "Συνδέσεις με άλλες σημειώσεις/ιδέες"),
            ("date", "Ημερομηνία"),
            ("status", "Κατάσταση: idea / draft / permanent"),
        ],
        "sections": [],
    },
    "general": {
        "label": "Γενικό (General)",
        "summary": "Catch-all για ό,τι δεν ταιριάζει αλλού· ελάχιστα μεταδεδομένα.",
        "fields": [
            ("title", "Τίτλος"),
            ("source", "Πηγή"),
            ("tags", "Ετικέτες"),
            ("date", "Ημερομηνία"),
            ("summary", "Σύντομη περίληψη"),
        ],
        "sections": [],
    },
}


def get(key: str) -> dict:
    p = dict(PROFILES[key])
    p["key"] = key
    p["db"] = str(db_path(key))
    return p


def all_profiles() -> list[dict]:
    return [get(k) for k in ORDER]


if __name__ == "__main__":
    for prof in all_profiles():
        print(f"\n### {prof['label']}  → {os.path.basename(prof['db'])}")
        print(f"    {prof['summary']}")
        for name, desc in prof["fields"]:
            print(f"    • {name}: {desc}")
