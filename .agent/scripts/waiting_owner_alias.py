#!/usr/bin/env python3
"""Owner-name normalisation for the batch files.

The waiting-on ledger stores the owner as free text, so one person appears under
several spellings and the chase queue splits them into separate bundles. This
map collapses only the cases that are certain. It does NOT merge Mohammad Ali
with Mohammad Albadarneh, or Teammate Abdelfattah with Teammate Ajana: different
people with similar names.
"""

ALIASES = {
    "Teammate Yuda": "Teammate Tri Yuda",
    "Teammate": "Teammate Tri Yuda",
    "Teammate Tri Yuda, Teammate Ur Rahman": "Teammate Tri Yuda",
    "Teammate Yuda and Teammate Rahman": "Teammate Tri Yuda",
    "Teammate Meer (ExampleVendor)": "Teammate Meer",
    "Teammate Meer / Teammate Tahir": "Teammate Meer",
    "Teammate Fakhouri and Teammate Meer": "Teammate Meer",
    "Teammate / Teammate / Teammate (ExampleVendor)": "Teammate Rasheed",
    "Teammate Chennupati and Alex Korobchuk": "Teammate Chennupati",
    "Teammate Singh": "Teammate Dev Singh",
    "Teammate Dev Singh / ExampleCo": "Teammate Dev Singh",
    "Teammate Dev Singh, Teammate Chennupati, Teammate Rahman": "Teammate Dev Singh",
    "Teammate Teammate": "Teammate Shahin",
    "Teammate (Teammate) Shahin": "Teammate Shahin",
    "Teammate Shahin": "Teammate Shahin",
    "Teammate Shahin and Teammate": "Teammate Shahin",
    "Ahmad Wshah, Teammate Hammouri, Tareq Abughoush": "Ahmad Wshah",
    "Ahmad Wshah and Mohammad Albadarneh": "Ahmad Wshah",
    "Fayez Hilow and Ahmad Wshah": "Fayez Abu Helow",
    "Rita": "Rita Alali",
    "Raouf Cherkawi / Thamer Bamieh": "Raouf Cherkawi",
    "YourManager Teammate Labna": "YourManager Teammate",
    "Hamza Shahbaz, Teammate Ismail, Amr AboKhalil (Finance)": "Hamza Shahbaz",
    "Teammate Al Helal": "Teammate Alhelal",
    "Teammate Helal": "Teammate Alhelal",
    "Lamya": "Lamya Al Tahrawe",
    "Lamya Tahrawe": "Lamya Al Tahrawe",
    "Teammate Alashqar": "Teammate Al Ashqar",
    "Fadi Jihad Abu Dyyeh": "Fadi Abu Dyyeh",
    "Ruba Al Jaikat": "Ruba Jaikat",
    "Ruba": "Ruba Jaikat",
    "Noha Youssef / Teammate Wahba": "Noha Youssef",
    "Fuad Abu Safi / Teammate Fakhouri": "Fuad Abu Safi",
    "Teammate Ajana": "Teammate Ajana",
    "Teammate Ajana / Teammate Ismail / Teammate Fakhouri": "Teammate Ajana",
    "Marta": "Marta Babiano",
    "Teammate Sanka, Teammate Chennupati, Teammate Meer": "Teammate Sanka",
    "Teammate & Teammate": "Teammate Raza",
    "Muhammad Ali": "Mohammad Ali",
}

def canonical(name):
    n = (name or "unknown").strip()
    return ALIASES.get(n, n)
