"""Semantic colors shared by nodes, arrows, labels, and group boundaries."""

THEMES = {
    "dark": {
        "background": "#1C2026",
        "text": "#E7EDF5",
        "muted": "#BBC2CF",
        "roles": {
            "default": ("#263244", "#8295AF"),
            "feature": ("#243438", "#56B6C2"),
            "state": ("#253344", "#61AFEF"),
            "condition": ("#332C40", "#C678DD"),
            "loss": ("#352E27", "#D19A66"),
            "frozen": ("#202834", "#8295AF"),
        },
    },
    "print": {
        "background": "#FFFFFF",
        "text": "#17212E",
        "muted": "#465568",
        "roles": {
            "default": ("#F0F3F7", "#52657D"),
            "feature": ("#E5F3F1", "#17675E"),
            "state": ("#EAF1FC", "#235BA0"),
            "condition": ("#F2EAF8", "#774392"),
            "loss": ("#FFF3DF", "#8B5718"),
            "frozen": ("#F4F4F4", "#626A75"),
        },
    },
}
