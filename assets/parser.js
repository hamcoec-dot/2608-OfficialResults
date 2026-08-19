/* ==========================================================================
   Hamilton County Election Commission - Election Data Parser
   Created: August 2026
   Standards: Pure ASCII, Robust State-Machine CSV Parsing, Zeros-Padding Matching
   ========================================================================== */

(function(global) {
  'use strict';

  function cleanContestName(name) {
    if (!name) return "";
    let cleaned = name.trim();
    // 1. Deduplicate consecutive identical words/phrases (case-insensitive)
    cleaned = cleaned.replace(/\b(.+?)\s+\1\b/gi, '$1');
    // 2. Fix wraparound repeats (e.g., 'District 9 District' -> 'District 9')
    const m = cleaned.match(/^(\w+)\s+(.+?)\s+\1$/i);
    if (m) {
      cleaned = `${m[1]} ${m[2]}`;
    }
    // 3. Normalize whitespace
    return cleaned.replace(/\s+/g, ' ').trim();
  }

  function getContestDefaultParty(contestName, rawParty) {
    const cUpper = (contestName || "").trim().toUpperCase();
    const rUpper = (rawParty || "").trim().toUpperCase();
    if (cUpper.startsWith('REP ') || cUpper.startsWith('REP\t') || rUpper === 'REP') {
      return 'REP';
    } else if (cUpper.startsWith('DEM ') || cUpper.startsWith('DEM\t') || rUpper === 'DEM') {
      return 'DEM';
    } else {
      return 'GEN';
    }
  }

  function getVoteForSeats(contestName) {
    if (!contestName) return 1;
    const m = contestName.match(/vote\s+for\s+(?:up\s+to\s+)?(\d+)/i);
    if (m) return parseInt(m[1], 10);
    const m2 = contestName.match(/\b(?:select|choose)\s+(\d+)\b/i);
    if (m2) return parseInt(m2[1], 10);
    return 1;
  }

  function extractCandidateParty(candidateName, contestParty) {
    const candTrimmed = (candidateName || "").trim();
    const candUpper = candTrimmed.toUpperCase();

    if (candUpper.indexOf('NO CANDIDATE') !== -1) {
      return { party: (contestParty === 'REP' || contestParty === 'DEM') ? contestParty : '', displayName: 'No Candidate Qualified' };
    } else if (candTrimmed === 'Retain' || candTrimmed === 'Replace') {
      return { party: 'NON', displayName: candTrimmed };
    }

    if (candTrimmed.indexOf('REP ') === 0) {
      return { party: 'REP', displayName: candTrimmed.substring(4).trim() };
    } else if (candTrimmed.indexOf('DEM ') === 0) {
      return { party: 'DEM', displayName: candTrimmed.substring(4).trim() };
    } else if (candTrimmed.indexOf('IND ') === 0) {
      return { party: 'IND', displayName: candTrimmed.substring(4).trim() };
    } else {
      if (contestParty === 'REP' || contestParty === 'DEM') {
        return { party: contestParty, displayName: candTrimmed };
      } else {
        return { party: 'IND', displayName: candTrimmed };
      }
    }
  }

  const ElectionParser = {
    cleanContestName: cleanContestName,
    getContestDefaultParty: getContestDefaultParty,
    /**
     * Parses standard CSV text using a state-machine loop.
     * Correctly handles quoted commas, escaped quotes (""), and line breaks.
     * @param {string} text - The raw CSV content.
     * @returns {Array<Array<string>>} 2D array of parsed cells.
     */
    parseCSV: function(text) {
      const lines = [];
      let row = [""];
      let inQuotes = false;
      
      for (let i = 0; i < text.length; i++) {
        const c = text[i];
        const next = text[i + 1];
        
        if (c === '"') {
          if (inQuotes && next === '"') {
            // Escaped quote
            row[row.length - 1] += '"';
            i++; // skip next quote
          } else {
            // Toggle quotes state
            inQuotes = !inQuotes;
          }
        } else if (c === ',' && !inQuotes) {
          row.push("");
        } else if ((c === '\r' || c === '\n') && !inQuotes) {
          if (c === '\r' && next === '\n') {
            i++; // handle CRLF
          }
          lines.push(row);
          row = [""];
        } else {
          row[row.length - 1] += c;
        }
      }
      
      if (row.length > 1 || row[0] !== "") {
        lines.push(row);
      }
      
      // Clean up whitespace inside cells
      return lines.map(r => r.map(cell => cell.trim()));
    },

    /**
     * Parses Detailed CSV lines containing precincts, counting groups, and county totals.
     * @param {Array<Array<string>>} lines - 2D CSV array.
     * @returns {Object} Structured data store.
     */
    processDetailedData: function(lines) {
      if (lines.length < 4) {
        throw new Error("Invalid CSV format: Too few rows.");
      }

      const contestsRow = lines[0];
      const partiesRow = lines[1];
      const candidatesRow = lines[2];
      
      const dataStore = {
        electionTitle: 'State Primary & County General Election',
        electionDate: 'August 6, 2026',
        precincts: {},
        contests: {},
        contestsOrder: [],
        precinctsOrder: [],
        groupsOrder: [],
        countySummary: {
          voters: 0,
          ballots: { total: 0, rep: 0, dem: 0, gen: 0 }
        }
      };

      // Detect dynamic start index for contest columns
      let contestStartIndex = 7;
      for (let idx = 0; idx < candidatesRow.length; idx++) {
        const val = (candidatesRow[idx] || "").trim().toUpperCase();
        if (val && val !== 'VOTERS' && val !== 'BALLOTS CAST' && val !== 'TOTAL' && val !== 'REGISTERED VOTERS') {
          contestStartIndex = idx;
          break;
        }
      }

      // Extract contests and candidates structure from headers
      for (let col = contestStartIndex; col < contestsRow.length; col++) {
        const contestName = cleanContestName(contestsRow[col]);
        const rawParty = (partiesRow[col] || "").trim();
        const partyName = getContestDefaultParty(contestName, rawParty);
        const candidateName = (candidatesRow[col] || "").trim();

        if (!contestName || !candidateName) continue;
        if (candidateName === "Write-in") continue;

        const candInfo = extractCandidateParty(candidateName, partyName);

        if (!dataStore.contestsOrder.includes(contestName)) {
          dataStore.contestsOrder.push(contestName);
        }

        if (!dataStore.contests[contestName]) {
          dataStore.contests[contestName] = {
            name: contestName,
            party: partyName,
            candidates: [],
            candidateInfo: {},
            countyTotals: {},
            columns: []
          };
        }

        if (!dataStore.contests[contestName].candidates.includes(candidateName)) {
          dataStore.contests[contestName].candidates.push(candidateName);
          dataStore.contests[contestName].candidateInfo[candidateName] = {
            rawName: candidateName,
            displayName: candInfo.displayName,
            party: candInfo.party
          };
        }

        dataStore.contests[contestName].columns.push({
          index: col,
          candidate: candidateName
        });
      }

      // Process rows 3+
      for (let r = 3; r < lines.length; r++) {
        const row = lines[r];
        if (row.length < 5) continue;

        const groupName = (row[0] || "").trim();
        const rawCode = (row[1] || "").trim();
        const precinctName = (row[2] || "").trim();

        if (!rawCode && !precinctName) continue;

        const precinctCode = rawCode.padStart(4, '0');
        const registeredVoters = parseInt(row[3]) || 0;
        const ballotsTotal = parseInt(row[4]) || 0;
        const ballotsRep = parseInt(row[5]) || 0;
        const ballotsDem = parseInt(row[6]) || 0;
        const ballotsGen = (contestStartIndex > 7 && row[7]) ? (parseInt(row[7]) || 0) : 0;

        // County totals row
        if (precinctCode === "0ZZZ" || precinctCode === "ZZZZ" || precinctName.toUpperCase() === "COUNTY TOTALS") {
          dataStore.countySummary.voters = registeredVoters;
          dataStore.countySummary.ballots.total = ballotsTotal;
          dataStore.countySummary.ballots.rep = ballotsRep;
          dataStore.countySummary.ballots.dem = ballotsDem;
          dataStore.countySummary.ballots.gen = ballotsGen;

          for (const contestName in dataStore.contests) {
            const contest = dataStore.contests[contestName];
            contest.columns.forEach(colMapping => {
              const votes = parseInt(row[colMapping.index]) || 0;
              contest.countyTotals[colMapping.candidate] = (contest.countyTotals[colMapping.candidate] || 0) + votes;
            });
          }
          continue;
        }

        if (!dataStore.precinctsOrder.includes(precinctCode)) {
          dataStore.precinctsOrder.push(precinctCode);
        }

        if (!dataStore.precincts[precinctCode]) {
          dataStore.precincts[precinctCode] = {
            code: precinctCode,
            name: precinctName,
            voters: registeredVoters,
            ballots: { total: 0, rep: 0, dem: 0, gen: 0 },
            results: {},
            groups: {}
          };
          for (const contestName in dataStore.contests) {
            dataStore.precincts[precinctCode].results[contestName] = {};
            dataStore.contests[contestName].candidates.forEach(cand => {
              dataStore.precincts[precinctCode].results[contestName][cand] = 0;
            });
          }
        }

        const precinct = dataStore.precincts[precinctCode];
        if (registeredVoters > 0 && precinct.voters === 0) precinct.voters = registeredVoters;

        precinct.ballots.total += ballotsTotal;
        precinct.ballots.rep += ballotsRep;
        precinct.ballots.dem += ballotsDem;
        precinct.ballots.gen += ballotsGen;

        for (const contestName in dataStore.contests) {
          const contest = dataStore.contests[contestName];
          contest.columns.forEach(colMapping => {
            const votes = parseInt(row[colMapping.index]) || 0;
            precinct.results[contestName][colMapping.candidate] += votes;
          });
        }

        if (groupName) {
          if (!dataStore.groupsOrder.includes(groupName)) {
            dataStore.groupsOrder.push(groupName);
          }

          if (!precinct.groups[groupName]) {
            precinct.groups[groupName] = {
              name: groupName,
              ballots: { total: ballotsTotal, rep: ballotsRep, dem: ballotsDem, gen: ballotsGen },
              results: {}
            };
          } else {
            precinct.groups[groupName].ballots.total += ballotsTotal;
            precinct.groups[groupName].ballots.rep += ballotsRep;
            precinct.groups[groupName].ballots.dem += ballotsDem;
            precinct.groups[groupName].ballots.gen += ballotsGen;
          }

          for (const contestName in dataStore.contests) {
            if (!precinct.groups[groupName].results[contestName]) {
              precinct.groups[groupName].results[contestName] = {};
            }
            const contest = dataStore.contests[contestName];
            contest.columns.forEach(colMapping => {
              const votes = parseInt(row[colMapping.index]) || 0;
              precinct.groups[groupName].results[contestName][colMapping.candidate] = votes;
            });
          }
        }
      }

      for (const contestName in dataStore.contests) {
        const contest = dataStore.contests[contestName];
        contest.voteFor = getVoteForSeats(contestName);
        contest.isNoCandidateQualified = contest.candidates.length === 1 && contest.candidates[0] === 'No Candidate Qualified';
        delete contest.columns;

        // Non-Partisan Normalization: If a contest has no REP or DEM candidates, all IND tags are stripped to "" (treated as non-partisan)
        const hasRepOrDem = Object.values(contest.candidateInfo || {}).some(info => info.party === 'REP' || info.party === 'DEM');
        if (!hasRepOrDem) {
          for (const cand in contest.candidateInfo) {
            if (contest.candidateInfo[cand].party === 'IND') {
              contest.candidateInfo[cand].party = '';
            }
          }
        }

        // Winner Resolution via Cutoff Vote calculation
        const seats = contest.voteFor || 1;
        const totals = contest.countyTotals || {};
        const activeCandidates = contest.candidates
          .filter(c => (totals[c] || 0) > 0)
          .sort((a, b) => (totals[b] || 0) - (totals[a] || 0));

        let cutoffVote = Infinity;
        if (activeCandidates.length > 0) {
          const cutoffIdx = Math.min(seats - 1, activeCandidates.length - 1);
          cutoffVote = totals[activeCandidates[cutoffIdx]] || 0;
        }

        contest.candidates.forEach(c => {
          const v = totals[c] || 0;
          const isWinner = v > 0 && v >= cutoffVote;
          if (contest.candidateInfo && contest.candidateInfo[c]) {
            contest.candidateInfo[c].isWinner = isWinner;
          }
        });
      }

      return dataStore;
    }
  };

  // Export parser to global window namespace
  global.ElectionParser = ElectionParser;

})(window);

