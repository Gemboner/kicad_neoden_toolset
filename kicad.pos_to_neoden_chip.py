from __future__ import print_function


#Translator for converting KiCAD .pos files to .csv files for a NEODEN pick and place machine
#Paste relevant rows of the .pos files below and chance the trailing_letter
#to be "T" for top or "B" for bottom.
#New coocrdinates are calculated according to user input.

import fileinput
import os
import sys
#import decimal

def transrotate(value):
	if value <= 180:
		return int(value)
	else:
		value -= 180
		return int(0-(180-value))

def process_pos_lines(pos_lists):
        output_string = "Designator,Footprint,Mid X,Mid Y,Layer,Rotation,Comment\n"
        output_string += ",,,,,,\n"
        for line in pos_lists:
                if line[0][0] == '#':
                        continue
                outline = line[0] + "," + line[2] + ","
                outline += line[3].split('.')[0] + "." + line[3].split('.')[1][:2] + "mm,"
                outline += line[4].split('.')[0] + "." + line[4].split('.')[1][:2] + "mm,"
                if line[-1] == "top":
                        outline += "T,"
                else:
                        outline += "B,"
                outline += str(transrotate(float(line[5]))) + "," + line[1]
                output_string += outline + '\n'
        return output_string

def main():
        if len(sys.argv) != 2:
                print("Syntax error!")
                print("Usage: python kicad.pos_to_neoden_chip.py your_position_file_from_kicad.pos")
                return
        #Turn input .pos file into a list of lists
        pos_lines = list()
        for line in fileinput.input():
                pos_lines.append(line.strip('\n').split())

        print("\nWe will offset positions according position of Chip_1 on Neoden")
        chip1xipos= input("Give Chip_1 X position: ")
        chip1yipos= input("Give Chip_1 Y position: ")
        cur_dir = os.getcwd()
        filename = fileinput.filename()
        if filename[-4:] != ".pos":
                print("WARNING: Input file doesn't have expected '.pos' extension")
        print("Parsing " + filename)
        
        print("Calculating Offset")
        #print("Chip_1 X: ",pos_lines[5][3])
        #print("Chip_1 Y: ",pos_lines[5][4])
        offsetxi= float(chip1xipos) - float(pos_lines[5][3])
        offsetyi= float(chip1yipos) - float(pos_lines[5][4])
        print ("X_offset: "+str(offsetxi)+" -- Y_offset: "+str(offsetyi))
        for line in pos_lines:
                if line[0][0] == '#':
                        continue
                line[3]= str('%.4f'%(offsetxi+float(line[3])))
                line[4]= str('%.4f'%(offsetyi+float(line[4])))
                #Get exactly 4 decimal places, like the other coordinates in .pos file
        
        neoden_format = process_pos_lines(pos_lines)
        #Strip trailing newline character
        if neoden_format[-2:] == '\n':
                neoden_format = neoden_format[:-2]

        
        print("Writing CSV file")
        output_file = os.path.splitext(os.path.join(cur_dir,filename))[0]+"_neoden.csv"

        with open(output_file, 'w') as ofile:
                ofile.write(neoden_format)
        print("Successfully wrote:",output_file)

if __name__ == '__main__':
        main()

