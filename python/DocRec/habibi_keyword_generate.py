import subprocess
import codecs

goal_goals = []
#txt_dirs = ['ted_originals/', 'ted_transcripts/']
#txt_dirs = ['orig_lower/']
txt_dirs = ['trans_preprocessed/', 'orig_preprocessed/']

for txt_dir in txt_dirs:
    with codecs.open('goal_goals.txt') as goal_goals_in:
        for line in goal_goals_in:
            if line[-1] == '\n':
                line = line[:-1]
            goal_goals.append((line.split(' ')[0],int(line.split(' ')[1])))

    for myfile,number in goal_goals:
        with codecs.open('TestMaryamTexts.m','r','utf-8') as mat_in:
            mat = mat_in.read()
            mat = mat.replace('<filename_placeholder>',txt_dir + myfile.split("/")[-1])
            mat = mat.replace('<number_placeholder>', '10') #str(number))

        with codecs.open('TestMaryamTexts2.m','w','utf-8') as mat_out:
            mat_out.write(mat)

        subprocess.call("touch W/2", shell=True)
        subprocess.call("touch W/1", shell=True)
        subprocess.call("matlab -r \"try;run('TestMaryamTexts2.m');catch;end;quit;\"", shell=True)
        subprocess.call("mv W/1 " + txt_dir + "habibi75_1/" + myfile.split("/")[-1], shell=True)
        subprocess.call("mv W/2 " + txt_dir + "habibi75_2/" + myfile.split("/")[-1], shell=True)
