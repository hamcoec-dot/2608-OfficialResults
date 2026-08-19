# ==========================================================================
# Hamilton County Election Commission - Election Data Bundler (Python 3)
# Dynamically adapts to ANY election CSV dataset (Primary, General, Special).
# Zero hardcoded filenames: Discovers dataset, detects dates, parties, ballot types.
# ==========================================================================

import csv
import datetime
import json
import os
import re

def read_csv_robust(file_path):
    """
    Robust reader that strips UTF-8 BOM automatically and falls back to cp1252
    if the CSV contains legacy Windows encoding characters.
    """
    try:
        with open(file_path, 'r', encoding='utf-8-sig') as f:
            return list(csv.reader(f))
    except UnicodeDecodeError:
        with open(file_path, 'r', encoding='cp1252') as f:
            return list(csv.reader(f))

def find_best_election_csv(data_dir):
    """
    Discovers election CSV files dynamically in the data directory.
    Picks the most detailed/complete dataset available.
    """
    if not os.path.exists(data_dir):
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    csv_files = [f for f in os.listdir(data_dir) if f.upper().endswith('.CSV')]
    if not csv_files:
        raise FileNotFoundError(f"No CSV election data files found in {data_dir}")

    # Prioritize detailed / with groups files if multiple CSVs exist
    def score_csv(name):
        upper = name.upper()
        score = 0
        if 'DETAILED' in upper: score += 10
        if 'WITH GROUPS' in upper or 'GROUPS' in upper: score += 5
        if 'EXPORT' in upper: score += 2
        try:
            score += os.path.getsize(os.path.join(data_dir, name)) / 1000000.0
        except OSError:
            pass
        return score

    csv_files.sort(key=score_csv, reverse=True)
    return os.path.join(data_dir, csv_files[0])

def parse_date_and_title(filename, parties_set):
    """
    Extracts election date and human-readable election title dynamically.
    """
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})', filename)
    if match:
        dt = datetime.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        date_str = dt.strftime('%B %d, %Y').replace(' 0', ' ')
        date_compact = dt.strftime('%Y%m%d')
        date_month_short = dt.strftime('%b')
        date_day = dt.strftime('%d')
    else:
        dt = datetime.date.today()
        date_str = dt.strftime('%B %d, %Y').replace(' 0', ' ')
        date_compact = dt.strftime('%Y%m%d')
        date_month_short = dt.strftime('%b')
        date_day = dt.strftime('%d')

    fn_upper = filename.upper()
    if 'STATE PRIMARY' in fn_upper and ('GENERAL' in fn_upper or 'GEN' in parties_set):
        title = 'State Primary & County General Election'
    elif 'STATE PRIMARY' in fn_upper:
        title = 'State Primary Election'
    elif 'COUNTY PRIMARY' in fn_upper:
        title = 'County Primary Election'
    elif 'PRESIDENTIAL' in fn_upper:
        title = 'Presidential & General Election'
    elif 'GENERAL' in fn_upper or ('GEN' in parties_set and 'REP' not in parties_set and 'DEM' not in parties_set):
        title = 'General Election'
    elif 'REP' in parties_set and 'DEM' in parties_set:
        title = 'Primary Election'
    else:
        title = 'Official Election Results'

    return {
        'date': date_str,
        'dateCompact': date_compact,
        'dateMonth': date_month_short,
        'dateDay': date_day,
        'title': title
    }

def clean_contest_name(name):
    """
    Deduplicates repeated district numbers/words in contest titles.
    e.g. 'County Commission, District 6 District 6' -> 'County Commission, District 6'
    """
    if not name:
        return ""
    cleaned = name.strip()
    # 1. Deduplicate consecutive identical words/phrases (case-insensitive)
    cleaned = re.sub(r'\b(.+?)\s+\1\b', r'\1', cleaned, flags=re.IGNORECASE)
    # 2. Fix wraparound repeats (e.g., 'District 9 District' -> 'District 9')
    m = re.match(r'^(\w+)\s+(.+?)\s+\1$', cleaned, flags=re.IGNORECASE)
    if m:
        cleaned = f"{m.group(1)} {m.group(2)}"
    # 3. Normalize whitespace
    return re.sub(r'\s+', ' ', cleaned).strip()

def get_contest_default_party(contest_name, raw_party):
    """
    Contest Title Default:
    - Starts with REP -> REP
    - Starts with DEM -> DEM
    - Otherwise -> GEN
    """
    c_upper = contest_name.strip().upper()
    r_upper = (raw_party or "").strip().upper()
    if c_upper.startswith('REP ') or c_upper.startswith('REP\t') or r_upper == 'REP':
        return 'REP'
    elif c_upper.startswith('DEM ') or c_upper.startswith('DEM\t') or r_upper == 'DEM':
        return 'DEM'
    else:
        return 'GEN'

def discover_seat_counts_from_txt(data_dir):
    """
    Parses EMS text exports (*.txt) in data_dir to extract exact vote-for seat counts (last 2 digits on result lines).
    """
    seat_map = {}
    if not os.path.exists(data_dir):
        return seat_map

    for fname in os.listdir(data_dir):
        if fname.lower().endswith('.txt'):
            fpath = os.path.join(data_dir, fname)
            try:
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        line_clean = line.rstrip('\r\n')
                        if len(line_clean) >= 150:
                            seats_str = line_clean[-2:].strip()
                            if seats_str.isdigit():
                                seats = int(seats_str)
                                contest_raw = line_clean[26:80].strip()
                                if contest_raw and not contest_raw.startswith('BALLOTS CAST'):
                                    c_clean = clean_contest_name(contest_raw)
                                    seat_map[c_clean] = seats
                                    norm_c = re.sub(r'\s+', ' ', c_clean).strip().lower()
                                    seat_map[norm_c] = seats
            except Exception as e:
                print(f"Notice: Could not parse text export {fname}: {e}")
    return seat_map

def get_vote_for_seats(contest_name, text_seat_map=None):
    """
    Extracts maximum allowed seats (N) by checking EMS text seat map or parsing voteFor patterns.
    """
    if not contest_name:
        return 1
    clean_c = clean_contest_name(contest_name)
    norm_c = re.sub(r'\s+', ' ', clean_c).strip().lower()

    if text_seat_map:
        if clean_c in text_seat_map:
            return text_seat_map[clean_c]
        if norm_c in text_seat_map:
            return text_seat_map[norm_c]
        for k, v in text_seat_map.items():
            if k.lower() in norm_c or norm_c in k.lower():
                return v

    m = re.search(r'vote\s+for\s+(?:up\s+to\s+)?(\d+)', contest_name, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m2 = re.search(r'\b(?:select|choose)\s+(\d+)\b', contest_name, re.IGNORECASE)
    if m2:
        return int(m2.group(1))

    if 'lookout mountain commission' in norm_c:
        return 5
    if 'lookout mountain school board' in norm_c:
        return 3

    return 1

def extract_candidate_party(candidate_name, contest_party):
    """
    Candidate Prefix Override:
    - REP <Name> -> party: REP, name: <Name>
    - DEM <Name> -> party: DEM, name: <Name>
    - IND <Name> -> party: IND, name: <Name>
    - NO CANDIDATE -> name: 'No Candidate Qualified', party inherited from contest (REP/DEM)
    """
    cand_trimmed = candidate_name.strip()
    cand_upper = cand_trimmed.upper()

    if "NO CANDIDATE" in cand_upper:
        return contest_party if contest_party in ['REP', 'DEM'] else '', 'No Candidate Qualified'
    elif cand_trimmed in ['Retain', 'Replace']:
        return 'NON', cand_trimmed

    if cand_trimmed.startswith('REP '):
        return 'REP', cand_trimmed[4:].strip()
    elif cand_trimmed.startswith('DEM '):
        return 'DEM', cand_trimmed[4:].strip()
    elif cand_trimmed.startswith('IND '):
        return 'IND', cand_trimmed[4:].strip()
    else:
        if contest_party in ['REP', 'DEM']:
            return contest_party, cand_trimmed
        else:
            return 'IND', cand_trimmed

def build_data():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base_dir, 'data')
    output_js_path = os.path.join(base_dir, 'assets', 'data.js')

    output_dir = os.path.dirname(output_js_path)
    if not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    csv_path = find_best_election_csv(data_dir)
    print(f"Loading Dynamic Election Dataset: {csv_path}")
    text_seat_map = discover_seat_counts_from_txt(data_dir)
    if text_seat_map:
        print(f"Discovered {len(text_seat_map)} contest seat rules from EMS text export.")
    reader = read_csv_robust(csv_path)

    if len(reader) < 4:
        raise ValueError(f"Invalid CSV structure in {csv_path}: insufficient rows.")

    contests_row = reader[0]
    parties_row = reader[1]
    candidates_row = reader[2]

    # Detect dynamic start index for contest columns
    contest_start_idx = 7
    for idx, val in enumerate(candidates_row):
        val_clean = val.strip().upper()
        if val_clean and val_clean not in ['VOTERS', 'BALLOTS CAST', 'TOTAL', 'REGISTERED VOTERS']:
            contest_start_idx = idx
            break

    print(f"Detected contest columns start index: {contest_start_idx}")

    # Discover ballot categories dynamically from columns 4 to (contest_start_idx - 1)
    ballot_categories = []
    for c in range(4, contest_start_idx):
        h_name = contests_row[c].strip() if c < len(contests_row) else f"Column_{c}"
        cand_h = candidates_row[c].strip() if c < len(candidates_row) else ""
        
        # Derive clean key and label
        h_upper = (h_name + " " + cand_h).upper()
        if 'REPUBLICAN' in h_upper or 'REP' in h_upper:
            key = 'rep'
            label = 'Republican Ballots'
        elif 'DEMOCRATIC' in h_upper or 'DEM' in h_upper:
            key = 'dem'
            label = 'Democratic Ballots'
        elif 'GENERAL' in h_upper or 'GEN' in h_upper:
            key = 'gen'
            label = 'General Ballots'
        elif 'TOTAL' in h_upper:
            key = 'total'
            label = 'Total Ballots'
        else:
            key = re.sub(r'[^a-z0-9]', '', h_name.lower()) or f"col_{c}"
            label = h_name

        ballot_categories.append({
            'index': c,
            'key': key,
            'label': label,
            'header': h_name
        })

    # Collect unique parties across contest columns
    parties_found = []
    for col in range(contest_start_idx, len(contests_row)):
        c_name = clean_contest_name(contests_row[col])
        raw_p = parties_row[col].strip() if col < len(parties_row) else ""
        p = get_contest_default_party(c_name, raw_p)
        if p not in parties_found:
            parties_found.append(p)

    metadata = parse_date_and_title(os.path.basename(csv_path), set(parties_found))
    print(f"Detected Election: {metadata['title']} ({metadata['date']})")
    print(f"Detected Parties: {', '.join(parties_found)}")
    print(f"Detected Ballot Categories: {[b['key'] for b in ballot_categories]}")

    data_store = {
        'electionTitle': metadata['title'],
        'electionDate': metadata['date'],
        'electionDateCompact': metadata['dateCompact'],
        'electionDateMonth': metadata['dateMonth'],
        'electionDateDay': metadata['dateDay'],
        'sourceFile': os.path.basename(csv_path),
        'parties': parties_found,
        'ballotCategories': ballot_categories,
        'precincts': {},
        'contests': {},
        'contestsOrder': [],
        'precinctsOrder': [],
        'groupsOrder': [],
        'countySummary': {
            'voters': 0,
            'ballots': {b['key']: 0 for b in ballot_categories}
        }
    }

    # Setup contests metadata in exact CSV column order
    for col in range(contest_start_idx, len(contests_row)):
        contest_name = clean_contest_name(contests_row[col])
        raw_party = parties_row[col].strip() if col < len(parties_row) else ""
        party_name = get_contest_default_party(contest_name, raw_party)
        raw_candidate_name = candidates_row[col].strip()

        if not contest_name or not raw_candidate_name:
            continue
        if raw_candidate_name == "Write-in":
            continue

        cand_party, display_name = extract_candidate_party(raw_candidate_name, party_name)

        if contest_name not in data_store['contestsOrder']:
            data_store['contestsOrder'].append(contest_name)

        if contest_name not in data_store['contests']:
            data_store['contests'][contest_name] = {
                'name': contest_name,
                'party': party_name,
                'voteFor': get_vote_for_seats(contests_row[col], text_seat_map),
                'candidates': [],
                'candidateInfo': {},
                'countyTotals': {},
                'columns': []
            }

        if raw_candidate_name not in data_store['contests'][contest_name]['candidates']:
            data_store['contests'][contest_name]['candidates'].append(raw_candidate_name)
            data_store['contests'][contest_name]['candidateInfo'][raw_candidate_name] = {
                'rawName': raw_candidate_name,
                'displayName': display_name,
                'party': cand_party
            }

        data_store['contests'][contest_name]['columns'].append({
            'index': col,
            'candidate': raw_candidate_name
        })

    # Process all rows
    for r in range(3, len(reader)):
        row = reader[r]
        if len(row) < 5:
            continue

        group_name = row[0].strip()
        raw_code = row[1].strip()
        precinct_name = row[2].strip()

        if not raw_code and not precinct_name:
            continue

        precinct_code = raw_code.zfill(4)
        registered_voters = int(row[3]) if len(row) > 3 and row[3].strip() else 0

        row_ballots = {}
        for b_cat in ballot_categories:
            c_i = b_cat['index']
            row_ballots[b_cat['key']] = int(row[c_i]) if c_i < len(row) and row[c_i].strip() else 0

        # Totals Row detection (ZZZ / COUNTY TOTALS)
        if precinct_code in ["0ZZZ", "ZZZZ", "00ZZZ", "ZZZ"] or "TOTALS" in precinct_name.upper():
            data_store['countySummary']['voters'] = registered_voters
            for b_cat in ballot_categories:
                data_store['countySummary']['ballots'][b_cat['key']] = row_ballots[b_cat['key']]

            for contest_name, contest in data_store['contests'].items():
                for col_mapping in contest['columns']:
                    c_idx = col_mapping['index']
                    votes = int(row[c_idx]) if c_idx < len(row) and row[c_idx].strip() else 0
                    cand = col_mapping['candidate']
                    contest['countyTotals'][cand] = contest['countyTotals'].get(cand, 0) + votes
            continue

        # Normal Precinct Group Row
        if precinct_code not in data_store['precinctsOrder']:
            data_store['precinctsOrder'].append(precinct_code)

        if precinct_code not in data_store['precincts']:
            data_store['precincts'][precinct_code] = {
                'code': precinct_code,
                'name': precinct_name,
                'voters': registered_voters,
                'ballots': {b['key']: 0 for b in ballot_categories},
                'results': {},
                'groups': {}
            }
            for contest_name, contest in data_store['contests'].items():
                data_store['precincts'][precinct_code]['results'][contest_name] = {
                    cand: 0 for cand in contest['candidates']
                }

        precinct = data_store['precincts'][precinct_code]
        if registered_voters > 0 and precinct['voters'] == 0:
            precinct['voters'] = registered_voters

        # Accumulate ballots
        for b_cat in ballot_categories:
            precinct['ballots'][b_cat['key']] += row_ballots[b_cat['key']]

        # Accumulate candidate votes
        for contest_name, contest in data_store['contests'].items():
            for col_mapping in contest['columns']:
                c_idx = col_mapping['index']
                votes = int(row[c_idx]) if c_idx < len(row) and row[c_idx].strip() else 0
                cand = col_mapping['candidate']
                precinct['results'][contest_name][cand] += votes

        # Record group breakdown
        if group_name:
            if group_name not in data_store['groupsOrder']:
                data_store['groupsOrder'].append(group_name)

            if group_name not in precinct['groups']:
                precinct['groups'][group_name] = {
                    'name': group_name,
                    'ballots': {b['key']: row_ballots[b['key']] for b in ballot_categories},
                    'results': {}
                }
            else:
                for b_cat in ballot_categories:
                    precinct['groups'][group_name]['ballots'][b_cat['key']] += row_ballots[b_cat['key']]

            for contest_name, contest in data_store['contests'].items():
                if contest_name not in precinct['groups'][group_name]['results']:
                    precinct['groups'][group_name]['results'][contest_name] = {}
                for col_mapping in contest['columns']:
                    c_idx = col_mapping['index']
                    votes = int(row[c_idx]) if c_idx < len(row) and row[c_idx].strip() else 0
                    cand = col_mapping['candidate']
                    precinct['groups'][group_name]['results'][contest_name][cand] = votes

    # 2. Non-Partisan Normalization, Winner Resolution, & Empty Contests Tagging
    for contest_name, contest in data_store['contests'].items():
        contest['isNoCandidateQualified'] = len(contest['candidates']) == 1 and contest['candidates'][0] == 'No Candidate Qualified'
        del contest['columns']

        # If a contest has no REP or DEM candidates, all IND tags are stripped to "" (treated as non-partisan, no badge displayed)
        has_rep_or_dem = any(info['party'] in ['REP', 'DEM'] for info in contest['candidateInfo'].values())
        if not has_rep_or_dem:
            for cand, info in contest['candidateInfo'].items():
                if info['party'] == 'IND':
                    info['party'] = ''

        # Winner Resolution using Cutoff Vote calculation
        seats = contest.get('voteFor', 1)
        totals = contest['countyTotals']
        active_candidates = [c for c in contest['candidates'] if totals.get(c, 0) > 0]
        active_candidates.sort(key=lambda c: totals.get(c, 0), reverse=True)

        if active_candidates:
            cutoff_idx = min(seats - 1, len(active_candidates) - 1)
            cutoff_vote = totals[active_candidates[cutoff_idx]]
        else:
            cutoff_vote = float('inf')

        for c in contest['candidates']:
            v = totals.get(c, 0)
            is_winner = (v > 0) and (v >= cutoff_vote)
            if c in contest['candidateInfo']:
                contest['candidateInfo'][c]['isWinner'] = is_winner

    # Reconciliation validation
    for b_cat in ballot_categories:
        k = b_cat['key']
        sum_val = sum(p['ballots'][k] for p in data_store['precincts'].values())
        tot_val = data_store['countySummary']['ballots'][k]
        assert sum_val == tot_val, f"Ballots mismatch for {k}: summed {sum_val} vs totals {tot_val}"

    print(f"Reconciliation Passed across all {len(ballot_categories)} ballot categories!")
    print(f"Writing compiled offline bundle to: {output_js_path}")

    json_data = json.dumps(data_store, indent=2)
    js_content = f"/* ==========================================================================\n" \
                 f"   Hamilton County Election Commission - Compiled Results Data\n" \
                 f"   Election: {data_store['electionTitle']} ({data_store['electionDate']})\n" \
                 f"   Dataset: {data_store['sourceFile']}\n" \
                 f"   ========================================================================== */\n\n" \
                 f"window.electionData = {json_data};\n"

    with open(output_js_path, 'w', encoding='utf-8') as f:
        f.write(js_content)

    print("Success! Static bundle created.")

if __name__ == '__main__':
    build_data()


