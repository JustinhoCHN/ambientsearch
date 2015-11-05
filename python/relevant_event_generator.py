#!/usr/bin/env python
# -*- coding: utf-8 -*-
__author__ = 'Benjamin Milde'

import redis

import keyword_extract
import wiki_search
import argparse
import json
import bisect
import re
from timer import Timer
from datetime import datetime

from bridge import KeywordClient

red = redis.StrictRedis()

#redis channel with relevant messages for this python module
my_redis_channel = 'ambient_transcript_only'

end_marker = '_end_'

# Helper class for addDisplayEntry and managing a list of displayed items with bisect:
# See: http://stackoverflow.com/questions/1344308/in-python-find-item-in-list-of-dicts-using-bisect 
class dict_list_index_get_member(object):
    def __init__(self, dict_list, member):
        self.dict_list = dict_list
        self.member = member
    def __getitem__(self, index):
        return self.dict_list[index][self.member]
    def __len__(self):
        return self.dict_list.__len__()

# Helper function for addDisplayEntry, the bisect module has unfortunafely no flag to reverse the ordering (we want bigger to lower values as sorting)
# Todo: choose more appropriate container, refactor
# See http://stackoverflow.com/questions/2247394/python-bisect-it-is-possible-to-work-with-descending-sorted-lists
def reverse_bisect(a, x, lo=0, hi=None):
    if lo < 0:
        raise ValueError('lo must be non-negative')
    if hi is None:
        hi = len(a)
    while lo < hi:
        mid = (lo+hi)//2
        if x > a[mid]: hi = mid
        else: lo = mid+1
    return lo

def idFromTitle(title):
    return re.sub(r'[^\w]', '_', title.replace(' ','_'))

# This event generator listens for incoming complete utterance redis events
# and starts the process of finding relevant information after each utterance with the keyword_extract module
# (relevant text and pictures are downloaded in a blocking manner using wiki_search)
class EventGenerator:

    def __init__(self,keyword_server_url, keyword_extrator, lang='en'):
        self.complete_transcript = []

        #dict with all relevant entries
        self.relevant_entries = {}
        #sorted list of displayed entries
        self.displayed_entries = []
        self.keyword_client = KeywordClient(keyword_server_url)
        self.ke = keyword_extrator
        self.lang = lang

    #Listen loop (redis)
    #Todo: for other languages than English, utf8 de and encoding will be needed
    def start_listen(self):
        pubsub = red.pubsub()
        pubsub.subscribe(my_redis_channel)
        for message in pubsub.listen():
            print 'New message:', message, type(message['data'])
            if type(message['data']) == str:
                json_message = json.loads(message['data'])
                if 'handle' in json_message:
                    if json_message['handle'] == 'completeUtterance':
                        print 'handle: completeUtterance'
                        self.complete_transcript.append(json_message['utterance'])
                        self.send_relevant_entry_updates()
                    if json_message['handle'] == 'closed':
                        print 'handle: closed'
                        self.delDisplayId(json_message['entry_id'])
                        print json_message
                    elif json_message['handle'] == 'reset':
                        print 'handle: reset all'
                        self.complete_transcript = []
                        self.relevant_entries = {}
                        self.displayed_entries = []
                        self.keyword_client.resetTimer()
                    elif json_message['handle'] == 'setLanguage':
                        print 'handle: set language' #todo

    # Add a relevant entry to the display, specify how many entries should be allowed maximally 
    def addDisplayEntry(self, entry_type, entry, max_entries=4):
        print 'check to add', entry['title'], entry['score']
        
        #TODO: Refactor the entry_type diretly into entry
        if 'type' not in entry:
            entry['type'] = entry_type

        if 'entry_id' not in entry:
            entry['entry_id'] = idFromTitle(entry['title'])

        #Determine position by its score
        displayed_entries_get_score = dict_list_index_get_member(self.displayed_entries,'score')
        insert_pos = reverse_bisect(displayed_entries_get_score, float(entry['score']))
        
        #Only add entry if we want to insert it into the max_entries best entries
        if insert_pos < max_entries:
            
            self.displayed_entries.insert(insert_pos, dict(entry))
            len_displayed_entries = len(self.displayed_entries)

            #In this case, one of the previous best entries needs to be deleted:
            if(len_displayed_entries > max_entries):
                #Send delete entry events to entries those score is below the four best showed entries
                for display_entry in self.displayed_entries[max_entries:]:
                    print 'del', display_entry['entry_id'], 'score fell below max_entries'
                    self.keyword_client.delRelevantEntry(display_entry['type'], display_entry['title'])

                self.displayed_entries = self.displayed_entries[:max_entries]
                len_displayed_entries = len(self.displayed_entries)

            if insert_pos == len_displayed_entries -1:
                insert_before = end_marker
                print 'Insert',entry['entry_id'],'at the end'
            else:
                insert_before = self.displayed_entries[insert_pos+1]['entry_id']
                print 'Insert',entry['entry_id'],'before',insert_before

            print 'add', entry['title'], entry['score']
            self.keyword_client.addRelevantEntry('wiki', entry['title'], entry['text'], entry['url'], entry['score'], insert_before)

        else:
            print "Insert pos is:", insert_pos, "below max_entries for",  entry["title"]

    # Delete a relevant entry from the display
    def delDisplayEntry(self, entry_type, title):
        print 'del',title
        for i,display_entry in list(enumerate(self.displayed_entries)):
            if (display_entry["title"] == title):
                print 'del', entry["title"]
                self.keyword_client.delRelevantEntry(entry_type, title)
                del self.displayed_entries[i]
                break

    # Delete a relevant entry from the model (without sending updates to the display)
    def delDisplayId(self, entry_id):
        print 'del id',entry_id
        for i,display_entry in list(enumerate(self.displayed_entries)):
            if (display_entry['entry_id'] == entry_id):
                print 'del', entry_id
                #self.keyword_client.delRelevantEntry(entry_type, title)
                del self.displayed_entries[i]
                break

    # Send relevant entry updates to the display, given a new full utterance. 
    # Also specify how many entries we want (max_entries) and how existing keywords should decay their score.
    def send_relevant_entry_updates(self,max_entries=4, decay=.8):

        print 'send_relevant_entry_updates called'
        with Timer() as t:

            #Do the decay for the displayed entries:
            #TODO: handle duplicate keywords and updated scores
            for entry in self.displayed_entries:
                entry["score"] *= decay

            keywords = self.ke.getKeywordsDruid(self.complete_transcript[-1])
            print keywords
            new_relevant_entries = wiki_search.getSummariesSingleKeyword(keywords,max_entries,lang=self.lang,pics_folder='pics/')

            new_relevant_entries_set = set(new_relevant_entries)
            relevant_entries_set = set(self.relevant_entries)

            #generate del relevant entries
            #for key in relevant_entries_set - new_relevant_entries_set:
            #    entry = self.relevant_entries[key]
            #    self.delDisplayEntry("wiki", entry["title"])
                
            #generate add relevant entries
            for key in new_relevant_entries_set - relevant_entries_set:
                entry = new_relevant_entries[key]
                self.addDisplayEntry("wiki", entry)

            #now look for changed scores (happens if a keyword got more important and gets mentioned again)   
            for key in (new_relevant_entries_set & relevant_entries_set):
                entry = new_relevant_entries[key]
                if entry["score"] > self.relevant_entries[key]["score"]:
                    print "score change for:",entry["title"], self.relevant_entries[key]["score"], "->", entry["score"]
                    found_displayed_entry = False
                    for display_entry in self.displayed_entries:
                        #already displayed, we could delete and read it, to reflect the new placement
                        if display_entry["title"] == key:
                            found_displayed_entry = True
                            #self.delDisplayEntry("wiki", entry["title"])
                            #self.addDisplayEntry("wiki", entry)
                            break

                    if not found_displayed_entry:
                        #not displayed, try to see if the higher score gets results in a document that is more important
                        self.addDisplayEntry("wiki", entry)

            for key in new_relevant_entries_set - relevant_entries_set:
                self.relevant_entries[key] = new_relevant_entries[key]

        print 'send_relevant_entry_updates finished. Time needed:', t.secs, 'seconds.'
        print 'Displayed entries should now be:',[entry['title'] for entry in self.displayed_entries]

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Command line client for kaldigstserver and the ambient visualization server')
    parser.add_argument('-a', '--ambient-uri', type=str, default='http://localhost:5000/', dest='ambient_uri', help='Ambient server websocket URI')
    parser.add_argument('-c', '--cutoff-druid-score', type=float, default=0.1, dest='cutoff_druid_score', help='Cutoff score for the druid algorithm, '
        'lower value will find more keywords, but takes longer to load and needs more memory')
    parser.add_argument('-l', '--language', type=str, default='en', dest='language', help='Select a language for the relevant evant geneartor (en,de). Defaults to en.')

    args = parser.parse_args()
    ke = keyword_extract.KeywordExtract(lang=args.language)
    ke.buildDruidCache(cutoff_druid_score=args.cutoff_druid_score)

    event_gen = EventGenerator(args.ambient_uri, ke, lang=args.language)
    event_gen.start_listen()
