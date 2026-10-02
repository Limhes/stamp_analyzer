
	# Handle the greys first, because they have no hues attached
	if chroma <= 0.5:
		if value >= 8.5:
			Level3Index = 263
		elseif value >= 6.5:
			Level3Index = 264
		elseif value >= 4.5:
			Level3Index = 265
		elseif value >= 2.5:
			Level3Index = 266
		else
			Level3Index = 267
		end
		
	else	# The input colour is not a grey.

		if hue >= 1 and hue < 4		#	Munsell hue range 1R-4R, p. 16 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 9
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 10
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5:
				Level3Index = 22
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5:
				Level3Index = 23
			elseif chroma > 0.5 and chroma <= 3 and value >= 2 and value < 3.5:
				Level3Index = 20
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 24
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 21
			elseif chroma > 1.5 and chroma <= 3 and value >= 8:
				Level3Index = 7
			elseif chroma > 1.5 and chroma <= 3 and value >= 6.5:
				Level3Index = 8
			elseif chroma > 3 and chroma <= 7 and value >= 8:
				Level3Index = 4
			elseif chroma > 3 and chroma <= 7 and value >= 6.5 :
				Level3Index = 5
			elseif chroma > 7 and chroma <= 11 and value >= 6.5 :
				Level3Index = 2
			elseif chroma > 11 and value >= 6.5 :
				Level3Index = 1
			elseif chroma > 1.5 and chroma <= 5 and value >= 5.5 and value < 6.5:
				Level3Index = 18
			elseif chroma > 5 and chroma <= 7 and value >= 5.5 and value < 6.5:
				Level3Index = 6
			elseif chroma > 7 and chroma <= 15 and value >= 5.5 and value < 6.5:
				Level3Index = 3
			elseif chroma > 1.5 and chroma <= 7 and value >= 3.5 and value < 5.5:
				Level3Index = 19
			elseif chroma > 7 and chroma <= 11 and value >= 3.5 and value < 5.5:
				Level3Index = 15
			elseif chroma > 11 and chroma <= 13 and value >= 3.5 and value < 5.5:
				Level3Index = 12
			elseif chroma >= 3 and chroma <= 9 and value >= 2 and value < 3.5:
				Level3Index = 16
			elseif chroma > 9 and chroma <= 11 and value >= 2 and value < 3.5:
				Level3Index = 13
			elseif chroma > 2 and chroma <= 7 and value < 2 :
				Level3Index = 17
			elseif chroma > 7 and chroma <= 11 and value < 2 :
				Level3Index = 14
			elseif chroma > 11 and value < 6.5 :
				Level3Index = 11
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 1 and 4)'])
			end
			
		elseif hue >= 4 and hue < 6		%	Munsell hue range 4R-6R, p. 16 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 9
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 10
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5:
				Level3Index = 22
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5:
				Level3Index = 23
			elseif chroma > 0.5 and chroma <= 3 and value >= 2 and value < 3.5:
				Level3Index = 20
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 24
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 21
			elseif chroma > 1.5 and chroma <= 3 and value >= 8:
				Level3Index = 7
			elseif chroma > 1.5 and chroma <= 3 and value >= 6.5:
				Level3Index = 8
			elseif chroma > 3 and chroma <= 7 and value >= 8:
				Level3Index = 4
			elseif chroma > 3 and chroma <= 7 and value >= 6.5 :
				Level3Index = 5
			elseif chroma > 7 and chroma <= 11 and value >= 6.5 :
				Level3Index = 26
			elseif chroma > 11 and value >= 6.5 :
				Level3Index = 25
			elseif chroma > 1.5 and chroma <= 5 and value >= 5.5 and value < 6.5:
				Level3Index = 18
			elseif chroma > 5 and chroma <= 7 and value >= 5.5 and value < 6.5:
				Level3Index = 6
			elseif chroma > 7 and chroma <= 11 and value >= 5.5 and value < 6.5:
				Level3Index = 3
			elseif chroma > 11 and chroma <= 15 and value >= 5.5 and value < 6.5:
				Level3Index = 27
			elseif chroma > 1.5 and chroma <= 7 and value >= 3.5 and value < 5.5:
				Level3Index = 19
			elseif chroma > 7 and chroma <= 11 and value >= 3.5 and value < 5.5:
				Level3Index = 15
			elseif chroma > 11 and chroma <= 13 and value >= 3.5 and value < 5.5:
				Level3Index = 12
			elseif chroma >= 3 and chroma <= 9 and value >= 2 and value < 3.5:
				Level3Index = 16
			elseif chroma > 9 and chroma <= 11 and value >= 2 and value < 3.5:
				Level3Index = 13
			elseif chroma > 2 and chroma <= 7 and value < 2 :
				Level3Index = 17
			elseif chroma > 7 and chroma <= 11 and value < 2 :
				Level3Index = 14
			elseif chroma > 11 and value < 6.5 :
				Level3Index = 11
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 4 and 6)'])
			end
			
		elseif hue >= 6 and hue < 7		%	Munsell hue range 6R-7R, p. 17 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 9
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 10
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5:
				Level3Index = 22
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5:
				Level3Index = 23
			elseif chroma > 0.5 and chroma <= 3 and value >= 1.5 and value < 2.5:
				Level3Index = 47
			elseif chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 3.5:
				Level3Index = 46
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 24
			elseif chroma > 1 and chroma <= 5 and value < 2.5:
				Level3Index = 44
			elseif chroma > 3 and chroma <= 7 and value >= 2.5 and value < 3.5:
				Level3Index = 43
			elseif chroma > 5 and chroma <= 7 and value < 2.5:
				Level3Index = 41
			elseif chroma > 1.5 and chroma <= 3 and value >= 8:
				Level3Index = 31
			elseif chroma > 1.5 and chroma <= 3 and value >= 6.5:
				Level3Index = 32
			elseif chroma > 3 and chroma <= 7 and value >= 8:
				Level3Index = 28
			elseif chroma > 3 and chroma <= 7 and value >= 6.5 :
				Level3Index = 29
			elseif chroma > 7 and chroma <= 11 and value >= 6.5 :
				Level3Index = 26
			elseif chroma > 11 and value >= 6.5 :
				Level3Index = 25
			elseif chroma > 1.5 and chroma <= 5 and value >= 5.5 and value < 6.5:
				Level3Index = 18
			elseif chroma > 5 and chroma <= 7 and value >= 5.5 and value < 6.5:
				Level3Index = 30
			elseif chroma > 7 and chroma <= 15 and value >= 5.5 and value < 6.5:
				Level3Index = 27
			elseif chroma > 1.5 and chroma <= 7 and value >= 3.5 and value < 5.5:
				Level3Index = 19
			elseif chroma > 7 and chroma <= 11 and value >= 3.5 and value < 5.5:
				Level3Index = 15
			elseif chroma > 11 and chroma <= 13 and value >= 3.5 and value < 5.5:
				Level3Index = 12
			elseif chroma >= 7 and chroma <= 9 and value >= 2 and value < 3.5:
				Level3Index = 16
			elseif chroma > 9 and chroma <= 11 and value >= 2 and value < 3.5:
				Level3Index = 13
			elseif chroma > 7 and chroma <= 11 and value < 2 :
				Level3Index = 14
			elseif chroma > 11 and value < 6.5 :
				Level3Index = 11
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 6 and 7)'])
			end
			
		elseif hue >= 7 and hue < 8		%	Munsell hue range 7R-8R, p. 17 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 9
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 10
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5:
				Level3Index = 22
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5:
				Level3Index = 23
			elseif chroma > 0.5 and chroma <= 3 and value >= 1.5 and value < 2.5:
				Level3Index = 47
			elseif chroma > 1.5 and chroma <= 3 and value >= 2.5 and value <= 3.5:
				Level3Index = 46
			elseif chroma > 1.5 and chroma <= 7 and value >= 3.5 and value <= 5.5:
				Level3Index = 19
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 24
			elseif chroma > 1 and chroma <= 5 and value < 2.5:
				Level3Index = 44
			elseif chroma > 3 and chroma <= 7 and value >= 2.5 and value < 3.5:
				Level3Index = 43
			elseif chroma > 5 and chroma <= 7 and value < 2.5:
				Level3Index = 41
			elseif chroma > 1.5 and chroma <= 3 and value >= 8:
				Level3Index = 31
			elseif chroma > 1.5 and chroma <= 3 and value >= 6.5:
				Level3Index = 32
			elseif chroma > 3 and chroma <= 7 and value >= 8:
				Level3Index = 28
			elseif chroma > 3 and chroma <= 7 and value >= 6.5 :
				Level3Index = 29
			elseif chroma > 7 and chroma <= 11 and value >= 6.5 :
				Level3Index = 26
			elseif chroma > 11 and value >= 6.5 :
				Level3Index = 25
			elseif chroma > 1.5 and chroma <= 5 and value >= 5.5 and value < 6.5:
				Level3Index = 18
			elseif chroma > 5 and chroma <= 7 and value >= 5.5 and value < 6.5:
				Level3Index = 30
			elseif chroma > 7 and chroma <= 11 and value >= 4.5 and value < 6.5:
				Level3Index = 37
			elseif chroma > 7 and chroma <= 11 and value >= 3.5 and value < 4.5:
				Level3Index = 38
			elseif chroma > 11 and chroma <= 13 and value >= 4.5 and value < 6.5:
				Level3Index = 35
			elseif chroma > 11 and chroma <= 13 and value >= 3.5 and value < 4.5:
				Level3Index = 36
			elseif chroma > 13 and value >= 4.5 and value < 6.5:
				Level3Index = 34
			elseif chroma >= 7 and chroma <= 9 and value >= 2 and value < 3.5:
				Level3Index = 16
			elseif chroma > 9 and chroma <= 11 and value >= 2 and value < 3.5:
				Level3Index = 13
			elseif chroma > 7 and chroma <= 11 and value < 2 :
				Level3Index = 14
			elseif chroma > 11 and value < 4.5 :
				Level3Index = 11
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 7 and 8)'])
			end
			
		elseif hue >= 8 and hue < 9		%	Munsell hue range 8R-9R, p. 18 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 9
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 10
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5:
				Level3Index = 22
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5:
				Level3Index = 23
			elseif chroma > 0.5 and chroma <= 3 and value >= 1.5 and value < 2.5:
				Level3Index = 47
			elseif chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 4.5:
				Level3Index = 46
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 24
			elseif chroma > 1 and chroma <= 5 and value < 2.5:
				Level3Index = 44
			elseif chroma > 3 and chroma <= 7 and value >= 2.5 and value < 4.5:
				Level3Index = 43
			elseif chroma > 5 and chroma <= 7 and value < 2.5:
				Level3Index = 41
			elseif chroma > 1.5 and chroma <= 3 and value >= 8:
				Level3Index = 31
			elseif chroma > 1.5 and chroma <= 3 and value >= 6.5:
				Level3Index = 32
			elseif chroma > 3 and chroma <= 7 and value >= 8:
				Level3Index = 28
			elseif chroma > 3 and chroma <= 7 and value >= 6.5 :
				Level3Index = 29
			elseif chroma > 7 and chroma <= 11 and value >= 6.5 :
				Level3Index = 26
			elseif chroma > 11 and value >= 6.5 :
				Level3Index = 25
			elseif chroma > 1.5 and chroma <= 3 and value >= 5.5 and value < 6.5:
				Level3Index = 18
			elseif chroma > 1.5 and chroma <= 3 and value >= 4.5 and value < 5.5:
				Level3Index = 19
			elseif chroma > 3 and chroma <= 5 and value >= 4.5 and value < 6.5:
				Level3Index = 42	;
			elseif chroma > 5 and chroma <= 7 and value >= 4.5 and value < 6.5:
				Level3Index = 39	;
			elseif chroma > 7 and chroma <= 11 and value >= 4.5 and value < 6.5:
				Level3Index = 37
			elseif chroma > 7 and chroma <= 11 and value >= 3.5 and value < 4.5:
				Level3Index = 38
			elseif chroma > 11 and chroma <= 13 and value >= 4.5 and value < 6.5:
				Level3Index = 35
			elseif chroma > 11 and chroma <= 13 and value >= 3.5 and value < 4.5:
				Level3Index = 36
			elseif chroma > 13 and value >= 4.5 and value < 6.5:
				Level3Index = 34
			elseif chroma >= 7 and chroma <= 9 and value >= 2 and value < 3.5:
				Level3Index = 16
			elseif chroma > 9 and chroma <= 11 and value >= 2 and value < 3.5:
				Level3Index = 13
			elseif chroma > 7 and chroma <= 11 and value < 2 :
				Level3Index = 14
			elseif chroma > 11 and value < 4.5 :
				Level3Index = 11
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 8 and 9)'])
			end
			
		elseif hue >= 9 and hue < 11		%	Munsell hue range 9R-1YR, p. 18 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 9
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 10
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5:
				Level3Index = 22
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5:
				Level3Index = 23
			elseif chroma > 0.5 and chroma <= 3 and value >= 1.5 and value < 2.5:
				Level3Index = 47
			elseif chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 4.5:
				Level3Index = 46
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 24
			elseif chroma > 1 and chroma <= 5 and value < 2.5:
				Level3Index = 44
			elseif chroma > 3 and chroma <= 7 and value >= 2.5 and value < 4.5:
				Level3Index = 43
			elseif chroma > 5 and value < 2.5:
				Level3Index = 41
			elseif chroma > 1.5 and chroma <= 3 and value >= 8:
				Level3Index = 31
			elseif chroma > 1.5 and chroma <= 3 and value >= 6.5:
				Level3Index = 32
			elseif chroma > 3 and chroma <= 7 and value >= 8:
				Level3Index = 28
			elseif chroma > 3 and chroma <= 7 and value >= 6.5 :
				Level3Index = 29
			elseif chroma > 7 and chroma <= 11 and value >= 6.5 :
				Level3Index = 26
			elseif chroma > 11 and value >= 6.5 :
				Level3Index = 25
			elseif chroma > 1.5 and chroma <= 3 and value >= 5.5 and value < 6.5:
				Level3Index = 18
			elseif chroma > 1.5 and chroma <= 3 and value >= 4.5 and value < 5.5:
				Level3Index = 19
			elseif chroma > 3 and chroma <= 5 and value >= 4.5 and value < 6.5:
				Level3Index = 42	;
			elseif chroma > 5 and chroma <= 7 and value >= 4.5 and value < 6.5:
				Level3Index = 39	;
			elseif chroma > 7 and chroma <= 11 and value >= 4.5 and value < 6.5:
				Level3Index = 37
			elseif chroma > 7 and chroma <= 11 and value >= 3.5 and value < 4.5:
				Level3Index = 38
			elseif chroma > 11 and chroma <= 13 and value >= 4.5 and value < 6.5:
				Level3Index = 35
			elseif chroma > 11 and chroma <= 13 and value >= 3.5 and value < 4.5:
				Level3Index = 36
			elseif chroma > 13 and value >= 3.5 and value < 6.5:
				Level3Index = 34
			elseif chroma >= 7 and value >= 2.5 and value < 3.5:
				Level3Index = 40
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 9 and 11)'])
			end
			
		elseif hue >= 11 and hue < 12		%	Munsell hue range 1YR-2YR, p. 19 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 9
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 10
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5:
				Level3Index = 22
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5:
				Level3Index = 64
			elseif chroma > 0.5 and chroma <= 3 and value >= 1.5 and value < 2.5:
				Level3Index = 47
			elseif chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 4.5:
				Level3Index = 46
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 65
			elseif chroma > 1 and chroma <= 5 and value < 2.5:
				Level3Index = 44
			elseif chroma > 3 and chroma <= 7 and value >= 2.5 and value < 4.5:
				Level3Index = 43
			elseif chroma > 5 and value < 2.5:
				Level3Index = 41
			elseif chroma > 1.5 and chroma <= 3 and value >= 8:
				Level3Index = 31
			elseif chroma > 1.5 and chroma <= 3 and value >= 6.5:
				Level3Index = 32
			elseif chroma > 3 and chroma <= 7 and value >= 8:
				Level3Index = 28
			elseif chroma > 3 and chroma <= 7 and value >= 6.5 :
				Level3Index = 29
			elseif chroma > 7 and chroma <= 11 and value >= 6.5 :
				Level3Index = 26
			elseif chroma > 11 and value >= 6.5 :
				Level3Index = 25
			elseif chroma > 1.5 and chroma <= 3 and value >= 4.5 and value < 6.5:
				Level3Index = 45
			elseif chroma > 3 and chroma <= 5 and value >= 4.5 and value < 6.5:
				Level3Index = 42	;
			elseif chroma > 5 and chroma <= 7 and value >= 4.5 and value < 6.5:
				Level3Index = 39	;
			elseif chroma > 7 and chroma <= 11 and value >= 4.5 and value < 6.5:
				Level3Index = 37
			elseif chroma > 7 and chroma <= 11 and value >= 3.5 and value < 4.5:
				Level3Index = 38
			elseif chroma > 11 and chroma <= 13 and value >= 4.5 and value < 6.5:
				Level3Index = 35
			elseif chroma > 11 and chroma <= 13 and value >= 3.5 and value < 4.5:
				Level3Index = 36
			elseif chroma > 13 and value >= 3.5 and value < 6.5:
				Level3Index = 34
			elseif chroma >= 7 and value >= 2.5 and value < 3.5:
				Level3Index = 40
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 11 and 12)'])
			end
			
		elseif hue >= 12 and hue < 13		%	Munsell hue range 2YR-3YR, p. 19 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 9
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 10
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5:
				Level3Index = 63
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5:
				Level3Index = 64
			elseif chroma > 0.5 and chroma <= 3 and value >= 1.5 and value < 2.5:
				Level3Index = 47
			elseif chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 4.5:
				Level3Index = 46
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 65
			elseif chroma > 1 and chroma <= 5 and value < 2.5:
				Level3Index = 44
			elseif chroma > 3 and chroma <= 5 and value >= 2.5 and value < 4.5:
				Level3Index = 43
			elseif chroma > 5 and value < 2.5:
				Level3Index = 56
			elseif chroma > 1.5 and chroma <= 3 and value >= 8:
				Level3Index = 31
			elseif chroma > 1.5 and chroma <= 3 and value >= 6.5:
				Level3Index = 32
			elseif chroma > 1.5 and chroma <= 3 and value >= 4.5 and value < 6.5:
				Level3Index = 45
			elseif chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 4.5:
				Level3Index = 46
			elseif chroma > 3 and chroma <= 6 and value >= 8:
				Level3Index = 28
			elseif chroma > 3 and chroma <= 6 and value >= 6.5 :
				Level3Index = 29
			elseif chroma > 6 and chroma <= 10 and value >= 7.5 :
				Level3Index = 52
			elseif chroma > 3 and chroma <= 5 and value >= 4.5 and value < 6.5:
				Level3Index = 42		;
			elseif chroma > 5 and chroma <= 7 and value >= 4.5 and value < 6.5:
				Level3Index = 39		;
			elseif chroma > 6 and chroma <= 10 and value >= 5.5 :
				Level3Index = 53
			elseif chroma > 7 and chroma <= 10 and value >= 4.5 :
				Level3Index = 54
			elseif chroma > 10 and chroma <= 14 and value >= 7.5 :
				Level3Index = 49
			elseif chroma > 10 and chroma <= 14 and value >= 5.5 :
				Level3Index = 50
			elseif chroma > 10 and chroma <= 14 and value >= 4.5 :
				Level3Index = 51
			elseif chroma > 14 and value >= 4.5:
				Level3Index = 48
			elseif chroma > 5 and value >= 2.5 and value < 4.5:
				Level3Index = 55
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 12 and 13)'])
			end
			
		elseif hue >= 13 and hue < 15		%	Munsell hue range 3YR-5YR, p. 20 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 9
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 10
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5:
				Level3Index = 63
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5:
				Level3Index = 64
			elseif chroma > 1.5 and chroma <= 2.5 and value >= 2.5 and value < 4.5:
				Level3Index = 61
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 65
			elseif chroma > 0.5 and chroma <= 2.5 and value >= 1.5 and value < 2.5:
				Level3Index = 62
			elseif chroma > 1 and chroma <= 5 and value < 2.5:
				Level3Index = 59
			elseif chroma > 2.5 and chroma <= 5 and value >= 2.5 and value < 4.5:
				Level3Index = 58
			elseif chroma > 5 and value < 2.5:
				Level3Index = 56
			elseif chroma > 1.5 and chroma <= 3 and value >= 8:
				Level3Index = 31
			elseif chroma > 1.5 and chroma <= 3 and value >= 6.5:
				Level3Index = 32
			elseif chroma > 1.5 and chroma <= 3 and value >= 4.5 and value < 6.5:
				Level3Index = 45
			elseif chroma > 3 and chroma <= 6 and value >= 8:
				Level3Index = 28
			elseif chroma > 3 and chroma <= 6 and value >= 6.5 :
				Level3Index = 29
			elseif chroma > 3 and chroma <= 6 and value >= 4.5 and value < 6.5:
				Level3Index = 57		;
			elseif chroma > 6 and chroma <= 10 and value >= 7.5 :
				Level3Index = 52
			elseif chroma > 6 and chroma <= 10 and value >= 5.5 :
				Level3Index = 53
			elseif chroma > 6 and chroma <= 10 and value >= 4.5 :
				Level3Index = 54
			elseif chroma > 10 and chroma <= 14 and value >= 7.5 :
				Level3Index = 49
			elseif chroma > 10 and chroma <= 14 and value >= 5.5 :
				Level3Index = 50
			elseif chroma > 10 and chroma <= 14 and value >= 4.5 :
				Level3Index = 51
			elseif chroma > 14 and value >= 4.5:
				Level3Index = 48
			elseif chroma > 5 and value >= 2.5 and value < 4.5:
				Level3Index = 55
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 13 and 15)'])
			end
			
		elseif hue >= 15 and hue < 17		%	Munsell hue range 5YR-7YR, p. 20 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.2 and value >= 8.5:
				Level3Index = 9
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 6.5:
				Level3Index = 10
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 4.5:
				Level3Index = 63
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 2.5:
				Level3Index = 64
			elseif chroma > 1.2 and chroma <= 2.5 and value >= 2.5 and value < 4.5:
				Level3Index = 61
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 65
			elseif chroma > 0.5 and chroma <= 2.5 and value >= 1.5 and value < 2.5:
				Level3Index = 62
			elseif chroma > 1 and chroma <= 5 and value < 2.5:
				Level3Index = 59
			elseif chroma > 2.5 and chroma <= 5 and value >= 2.5 and value < 4.5:
				Level3Index = 58
			elseif chroma > 5 and value < 2.5:
				Level3Index = 56
			elseif chroma > 1.2 and chroma <= 3 and value >= 8:
				Level3Index = 31
			elseif chroma > 1.2 and chroma <= 3 and value >= 6.5:
				Level3Index = 33
			elseif chroma > 1.2 and chroma <= 3 and value >= 4.5 and value < 6.5:
				Level3Index = 60
			elseif chroma > 3 and chroma <= 6 and value >= 8:
				Level3Index = 28
			elseif chroma > 3 and chroma <= 6 and value >= 6.5 :
				Level3Index = 29
			elseif chroma > 3 and chroma <= 6 and value >= 4.5 and value < 6.5:
				Level3Index = 57		;
			elseif chroma > 6 and chroma <= 10 and value >= 7.5 :
				Level3Index = 52
			elseif chroma > 6 and chroma <= 10 and value >= 5.5 :
				Level3Index = 53
			elseif chroma > 6 and chroma <= 10 and value >= 4.5 :
				Level3Index = 54
			elseif chroma > 10 and chroma <= 14 and value >= 7.5 :
				Level3Index = 49
			elseif chroma > 10 and chroma <= 14 and value >= 5.5 :
				Level3Index = 50
			elseif chroma > 10 and chroma <= 14 and value >= 4.5 :
				Level3Index = 51
			elseif chroma > 14 and value >= 4.5:
				Level3Index = 48
			elseif chroma > 5 and value >= 2.5 and value < 4.5:
				Level3Index = 55
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 15 and 17)'])
			end
			
		elseif hue >= 17 and hue < 18		%	Munsell hue range 7YR-8YR, p. 21 of [Kelly1976]:
			if chroma > 0.7 and chroma <= 1.2 and value >= 8.5:
				Level3Index = 92
			elseif chroma > 0.7 and chroma <= 1.2 and value >= 6.5:
				Level3Index = 93
			elseif chroma > 0.7 and chroma <= 1.2 and value >= 4.5:
				Level3Index = 63
			elseif value >= 8.5 and chroma <= 0.7:
				Level3Index = 263
			elseif value >= 6.5 and chroma <= 0.7:
				Level3Index = 264
			elseif value >= 4.5 and chroma <= 0.7:
				Level3Index = 265
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 2.5 and value < 4.5:
				Level3Index = 64
			elseif chroma > 1.2 and chroma <= 2.5 and value >= 2.5 and value < 4.5:
				Level3Index = 61
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 65
			elseif chroma > 0.5 and chroma <= 2.5 and value >= 1.5 and value < 2.5:
				Level3Index = 62
			elseif chroma > 1 and chroma <= 5 and value < 2.5:
				Level3Index = 59
			elseif chroma > 2.5 and chroma <= 5 and value >= 2.5 and value < 4.5:
				Level3Index = 58
			elseif chroma > 1.2 and chroma <= 3 and value >= 8:
				Level3Index = 31
			elseif chroma > 1.2 and chroma <= 3 and value >= 6.5:
				Level3Index = 33
			elseif chroma > 1.2 and chroma <= 3 and value >= 4.5 and value < 6.5:
				Level3Index = 60
			elseif chroma > 3 and chroma <= 6 and value >= 7.5:
				Level3Index = 73
			elseif chroma > 3 and chroma <= 6 and value >= 6.5 :
				Level3Index = 76
			elseif chroma > 3 and chroma <= 6 and value >= 4.5 and value < 6.5:
				Level3Index = 57		;
			elseif chroma > 6 and chroma <= 10 and value >= 8 :
				Level3Index = 70
			elseif chroma > 6 and chroma <= 10 and value >= 6.5 :
				Level3Index = 71
			elseif chroma > 6 and chroma <= 10 and value >= 5.5 :
				Level3Index = 72
			elseif chroma > 10 and chroma <= 14 and value >= 8 :
				Level3Index = 67
			elseif chroma > 10 and chroma <= 14 and value >= 6.5 :
				Level3Index = 68
			elseif chroma > 10 and chroma <= 14 and value >= 5.5 :
				Level3Index = 69
			elseif chroma > 6 and value >= 4.5 and value < 5.5:
				Level3Index = 74
			elseif chroma > 14 and value >= 5.5:
				Level3Index = 66
			elseif chroma > 5 and value >= 2.5 and value < 4.5:
				Level3Index = 55
			elseif chroma > 5 and value < 2.5:
				Level3Index = 56
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 17 and 18)'])
			end
			
		elseif hue >= 18 and hue < 21		%	Munsell hue range 8YR-1Y, p. 21 of [Kelly1976]:
			if chroma > 0.7 and chroma <= 2 and value >= 8.5:
				Level3Index = 92
			elseif chroma > 0.7 and chroma <= 2 and value >= 6.5:
				Level3Index = 93
			elseif chroma > 0.7 and chroma <= 1.2 and value >= 4.5 and value < 6.5:
				Level3Index = 63
			elseif value >= 8.5 and chroma <= 0.7:
				Level3Index = 263
			elseif value >= 6.5 and chroma <= 0.7:
				Level3Index = 264
			elseif value >= 4.5 and chroma <= 0.7:
				Level3Index = 265
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 2.5 and value < 4.5:
				Level3Index = 64
			elseif chroma > 0.5 and chroma <= 1 and value <= 1.5:
				Level3Index = 65
			elseif chroma > 2 and chroma <= 6 and value >= 7.5:
				Level3Index = 73
			elseif chroma > 1.2 and chroma <= 3 and value >= 5.5 and value < 7.5:
				Level3Index = 79
			elseif chroma > 3 and chroma <= 6 and value >= 5.5 and value < 7.5:
				Level3Index = 76
			elseif (chroma > 1.2 and chroma <= 3 and value >= 4.5 and value < 5.5) || ...:
					(chroma > 1.2 and chroma <= 2.5 and value >= 3.5 and value < 4.5)
				Level3Index = 80
			elseif (chroma > 3 and chroma <= 5 and value >= 4.5 and value < 5.5) || ...:
					(chroma > 2.5 and chroma <= 5 and value >= 3.5 and value < 4.5)
				Level3Index = 77
			elseif (chroma > 1.2 and chroma <= 2.5 and value >= 2.5 and value < 3.5) || ...:
					(chroma > 0.5 and chroma <= 2.5 and value >= 1.5 and value < 2.5)
				Level3Index = 81
			elseif chroma > 1 and chroma <= 5 and value < 3.5:
				Level3Index = 78
			elseif chroma > 6 and chroma <= 10 and value >= 8 :
				Level3Index = 70
			elseif chroma > 6 and chroma <= 10 and value >= 6.5 :
				Level3Index = 71
			elseif chroma > 6 and chroma <= 10 and value >= 5.5 :
				Level3Index = 72
			elseif chroma > 10 and chroma <= 14 and value >= 8 :
				Level3Index = 67
			elseif chroma > 10 and chroma <= 14 and value >= 6.5 :
				Level3Index = 68
			elseif chroma > 10 and chroma <= 14 and value >= 5.5 :
				Level3Index = 69
			elseif chroma > 14 and value >= 5.5:
				Level3Index = 66
			elseif chroma > 5 and value >= 3.5 and value < 5.5:
				Level3Index = 74
			elseif chroma > 5 and value < 3.5:
				Level3Index = 75
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 18 and 21)'])
			end
			
		elseif hue >= 21 and hue < 24		%	Munsell hue range 1Y-4Y, p. 22 of [Kelly1976]:
			if chroma > 0.7 and chroma <= 2 and value >= 8.5:
				Level3Index = 92
			elseif chroma > 0.7 and chroma <= 2 and value >= 6.5:
				Level3Index = 93
			elseif chroma > 0.7 and chroma <= 1.2 and value >= 4.5 and value < 6.5:
				Level3Index = 63
			elseif value >= 8.5 and chroma <= 0.7:
				Level3Index = 263
			elseif value >= 6.5 and chroma <= 0.7:
				Level3Index = 264
			elseif value >= 4.5 and chroma <= 0.7:
				Level3Index = 265
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 2.5 and value < 4.5:
				Level3Index = 64
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 65
			elseif chroma > 2 and chroma <= 5 and value >= 8:
				Level3Index = 89
			elseif chroma > 2 and chroma <= 5 and value >= 6.5:
				Level3Index = 90
			elseif chroma > 3 and chroma <= 5 and value >= 5.5 and value < 6.5:
				Level3Index = 91
			elseif chroma > 5 and chroma <= 8 and value >= 8:
				Level3Index = 86
			elseif chroma > 5 and chroma <= 8 and value >= 6.5:
				Level3Index = 87
			elseif chroma > 5 and chroma <= 8 and value >= 5.5:
				Level3Index = 88
			elseif chroma > 8 and chroma <= 11 and value >= 8:
				Level3Index = 83
			elseif chroma > 8 and chroma <= 11 and value >= 6.5:
				Level3Index = 84
			elseif chroma > 8 and chroma <= 11 and value >= 5.5:
				Level3Index = 85
			elseif chroma > 11 and value >= 5.5:
				Level3Index = 82
			elseif chroma > 1.2 and value >= 4.5 and value < 6.5:
				Level3Index = 94
			elseif chroma > 1.2 and value >= 2.5 and value < 4.5:
				Level3Index = 95
			elseif chroma > 0.5 and value < 2.5:
				Level3Index = 96
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 21 and 24)'])
			end
			
		elseif hue >= 24 and hue < 27		%	Munsell hue range 4Y-7Y, p. 22 of [Kelly1976]:
			if chroma > 0.7 and chroma <= 2 and value >= 8.5:
				Level3Index = 92
			elseif chroma > 0.7 and chroma <= 2 and value >= 6.5:
				Level3Index = 93
			elseif chroma > 0.7 and chroma <= 2 and value >= 4.5 and value < 6.5:
				Level3Index = 112
			elseif chroma > 2 and chroma <= 3 and value >= 4.5 and value < 6.5:
				Level3Index = 109
			elseif value >= 8.5 and chroma <= 0.7:
				Level3Index = 263
			elseif value >= 6.5 and chroma <= 0.7:
				Level3Index = 264
			elseif value >= 4.5 and chroma <= 0.7:
				Level3Index = 265
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5 and value < 4.5:
				Level3Index = 113
			elseif chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 4.5:
				Level3Index = 110
			elseif chroma > 0.5 and chroma <= 3 and value >= 1.5 and value < 2.5:
				Level3Index = 111
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 114
			elseif chroma > 2 and chroma <= 5 and value >= 8:
				Level3Index = 89
			elseif chroma > 2 and chroma <= 5 and value >= 6.5:
				Level3Index = 90
			elseif chroma > 3 and chroma <= 5 and value >= 5.5 and value < 6.5:
				Level3Index = 91
			elseif chroma > 5 and chroma <= 8 and value >= 8:
				Level3Index = 86
			elseif chroma > 5 and chroma <= 8 and value >= 6.5:
				Level3Index = 87
			elseif chroma > 5 and chroma <= 8 and value >= 5.5:
				Level3Index = 88
			elseif chroma > 8 and chroma <= 11 and value >= 8:
				Level3Index = 83
			elseif chroma > 8 and chroma <= 11 and value >= 6.5:
				Level3Index = 84
			elseif chroma > 8 and chroma <= 11 and value >= 5.5:
				Level3Index = 85
			elseif chroma > 11 and value >= 5.5:
				Level3Index = 82
			elseif chroma > 3 and value >= 4.5 and value < 5.5:
				Level3Index = 106
			elseif chroma > 3 and value >= 2.5 and value < 4.5:
				Level3Index = 107
			elseif chroma > 0.5 and value < 2.5:
				Level3Index = 108
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 24 and 27)'])
			end
			
		elseif hue >= 27 and hue < 29		%	Munsell hue range 7Y-9Y, p. 23 of [Kelly1976]:
			if chroma > 0.7 and chroma <= 2 and value >= 8.5:
				Level3Index = 92
			elseif chroma > 0.7 and chroma <= 2 and value >= 6.5:
				Level3Index = 93
			elseif chroma > 0.7 and chroma <= 2 and value >= 4.5 and value < 6.5:
				Level3Index = 112
			elseif chroma > 2 and chroma <= 3 and value >= 8:
				Level3Index = 89
			elseif chroma > 2 and chroma <= 3 and value >= 6.5:
				Level3Index = 90
			elseif chroma > 2 and chroma <= 3 and value >= 4.5 and value < 6.5:
				Level3Index = 109
			elseif value >= 8.5 and chroma <= 0.7:
				Level3Index = 263
			elseif value >= 6.5 and chroma <= 0.7:
				Level3Index = 264
			elseif value >= 4.5 and chroma <= 0.7:
				Level3Index = 265
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5 and value < 4.5:
				Level3Index = 113
			elseif chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 4.5:
				Level3Index = 110
			elseif chroma > 0.5 and chroma <= 3 and value >= 1.5 and value < 2.5:
				Level3Index = 111
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 114
			elseif chroma > 3 and chroma <= 5 and value >= 8:
				Level3Index = 104
			elseif chroma > 3 and chroma <= 5 and value >= 6.5:
				Level3Index = 105
			elseif chroma > 5 and chroma <= 8 and value >= 8:
				Level3Index = 101
			elseif chroma > 5 and chroma <= 8 and value >= 6.5:
				Level3Index = 102
			elseif chroma > 5 and chroma <= 8 and value >= 5.5:
				Level3Index = 103
			elseif chroma > 8 and chroma <= 11 and value >= 8:
				Level3Index = 98
			elseif chroma > 8 and chroma <= 11 and value >= 6.5:
				Level3Index = 99
			elseif chroma > 8 and chroma <= 11 and value >= 5.5:
				Level3Index = 100
			elseif chroma > 11 and value >= 5.5:
				Level3Index = 97
			elseif chroma > 3 and value >= 4.5 and value < 6.5:
				Level3Index = 106
			elseif chroma > 3 and value >= 2.5 and value < 4.5:
				Level3Index = 107
			elseif chroma > 0.5 and value < 2.5:
				Level3Index = 108
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 27 and 29)'])
			end
			
		elseif hue >= 29 and hue < 32		%	Munsell hue range 9Y-2GY, p. 23 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.2 and value >= 8.5:
				Level3Index = 92
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 6.5:
				Level3Index = 93
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 4.5 and value < 6.5:
				Level3Index = 112
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 2.5 and value < 4.5:
				Level3Index = 113
			elseif chroma > 1.2 and chroma <= 3 and value >= 7.5:
				Level3Index = 121
			elseif chroma > 1.2 and chroma <= 3 and value >= 6.5:
				Level3Index = 122
			elseif chroma > 1.2 and chroma <= 3 and value >= 4.5 and value < 6.5:
				Level3Index = 109
			elseif chroma > 1.2 and chroma <= 3 and value >= 2.5 and value < 4.5:
				Level3Index = 110
			elseif chroma > 0.5 and chroma <= 3 and value >= 1.5 and value < 2.5:
				Level3Index = 111
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 114
			elseif chroma > 3 and chroma <= 5 and value >= 8:
				Level3Index = 104
			elseif chroma > 3 and chroma <= 5 and value >= 6.5:
				Level3Index = 105
			elseif chroma > 5 and chroma <= 8 and value >= 8:
				Level3Index = 101
			elseif chroma > 5 and chroma <= 8 and value >= 6.5:
				Level3Index = 102
			elseif chroma > 5 and chroma <= 8 and value >= 5.5:
				Level3Index = 103
			elseif chroma > 8 and chroma <= 11 and value >= 8:
				Level3Index = 98
			elseif chroma > 8 and chroma <= 11 and value >= 6.5:
				Level3Index = 99
			elseif chroma > 8 and chroma <= 11 and value >= 5.5:
				Level3Index = 100
			elseif chroma > 11 and value >= 5.5:
				Level3Index = 97
			elseif chroma > 3 and value >= 4.5 and value < 6.5:
				Level3Index = 106
			elseif chroma > 3 and value >= 2.5 and value < 4.5:
				Level3Index = 107
			elseif chroma > 0.5 and value < 2.5:
				Level3Index = 108
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 29 and 32)'])
			end
			
		elseif hue >= 32 and hue < 34		%	Munsell hue range 2GY-4GY, p. 24 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.2 and value >= 8.5:
				Level3Index = 92
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 6.5:
				Level3Index = 93
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 4.5 and value < 6.5:
				Level3Index = 112
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 2.5 and value < 4.5:
				Level3Index = 113
			elseif chroma > 1.2 and chroma <= 3 and value >= 7.5:
				Level3Index = 121
			elseif chroma > 1.2 and chroma <= 3 and value >= 4.5:
				Level3Index = 122
			elseif chroma > 1.2 and chroma <= 3 and value >= 2.5:
				Level3Index = 127
			elseif chroma > 0.5 and chroma <= 3 and value >= 1.5 and value < 2.5:
				Level3Index = 128
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 114
			elseif chroma > 3 and chroma <= 7 and value >= 7.5:
				Level3Index = 119
			elseif chroma > 3 and chroma <= 7 and value >= 4.5:
				Level3Index = 120
			elseif chroma > 3 and chroma <= 7 and value >= 2.5:
				Level3Index = 125
			elseif chroma > 7 and chroma <= 11 and value >= 7.5:
				Level3Index = 116
			elseif chroma > 7 and chroma <= 11 and value >= 4.5:
				Level3Index = 117
			elseif chroma > 7 and chroma <= 11 and value >= 3.5:
				Level3Index = 118
			elseif chroma > 7 and value >= 2.5 and value < 3.5:
				Level3Index = 123
			elseif chroma > 11 and value >= 3.5:
				Level3Index = 115
			elseif chroma > 7 and value < 2.5:
				Level3Index = 124
			elseif chroma > 1 and value < 2.5:
				Level3Index = 126
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 32 and 34)'])
			end																																																																																																																																																																																																																																								
			
		elseif hue >= 34 and hue < 38		%	Munsell hue range 4GY-8GY, p. 24 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.2 and value >= 8.5:
				Level3Index = 153
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 6.5:
				Level3Index = 154
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 4.5 and value < 6.5:
				Level3Index = 155
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 2.5 and value < 4.5:
				Level3Index = 156
			elseif chroma > 1.2 and chroma <= 3 and value >= 7.5:
				Level3Index = 121
			elseif chroma > 1.2 and chroma <= 3 and value >= 4.5:
				Level3Index = 122
			elseif chroma > 1.2 and chroma <= 3 and value >= 2.5:
				Level3Index = 127
			elseif chroma > 0.5 and chroma <= 3 and value >= 1.5 and value < 2.5:
				Level3Index = 128
			elseif chroma > 0.5 and chroma <= 1 and value < 1.5:
				Level3Index = 157
			elseif chroma > 3 and chroma <= 7 and value >= 7.5:
				Level3Index = 119
			elseif chroma > 3 and chroma <= 7 and value >= 4.5:
				Level3Index = 120
			elseif chroma > 3 and chroma <= 7 and value >= 2.5:
				Level3Index = 125
			elseif chroma > 7 and chroma <= 11 and value >= 7.5:
				Level3Index = 116
			elseif chroma > 7 and chroma <= 11 and value >= 4.5:
				Level3Index = 117
			elseif chroma > 7 and chroma <= 11 and value >= 3.5:
				Level3Index = 118
			elseif chroma > 7 and value >= 2.5 and value < 3.5:
				Level3Index = 123
			elseif chroma > 11 and value >= 3.5:
				Level3Index = 115
			elseif chroma > 7 and value < 2.5:
				Level3Index = 124
			elseif chroma > 1 and value < 2.5:
				Level3Index = 126
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 34 and 38)'])
			end																																																																																																																																																																																																																																								
						
		elseif hue >= 38 and hue < 43		%	Munsell hue range 8GY-3G, p. 25 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.2 and value >= 8.5:
				Level3Index = 153
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 6.5:
				Level3Index = 154
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 4.5 and value < 6.5:
				Level3Index = 155
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 2.5 and value < 4.5:
				Level3Index = 156
			elseif chroma > 1.2 and chroma <= 2.5 and value >= 7.5:
				Level3Index = 148
			elseif chroma > 1.2 and chroma <= 2.5 and value >= 5.5:
				Level3Index = 149
			elseif chroma > 1.2 and chroma <= 2.5 and value >= 3.5:
				Level3Index = 150
			elseif (chroma > 1 and chroma <= 2.5 and value >= 2.5 and value < 3.5) || ...:
				   (chroma > 0.5 and chroma <= 2 and value >= 2 and value < 2.5)
				Level3Index = 151
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 157
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 152
			elseif chroma > 2.5 and chroma <= 7 and value >= 8.5:
				Level3Index = 134
			elseif chroma > 2.5 and chroma <= 7 and value >= 6.5:
				Level3Index = 135
			elseif chroma > 2.5 and chroma <= 7 and value >= 4.5:
				Level3Index = 136
			elseif chroma > 2.5 and chroma <= 7 and value >= 2.5:
				Level3Index = 137
			elseif chroma > 2 and chroma <= 7 and value < 2.5:
				Level3Index = 138
			elseif chroma > 7 and chroma <= 11 and value >= 6.5:
				Level3Index = 130
			elseif chroma > 7 and chroma <= 11 and value >= 4.5:
				Level3Index = 131
			elseif chroma > 7 and value >= 2.5 and value <= 4.5:
				Level3Index = 132
			elseif chroma > 7 and value < 2.5 :
				Level3Index = 133
			elseif chroma > 11 and value > 4.5 :
				Level3Index = 129
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 38 and 43)'])
			end
			
		elseif hue >= 43 and hue < 49		%	Munsell hue range 3G-9G, p. 25 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.2 and value >= 8.5:
				Level3Index = 153
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 6.5:
				Level3Index = 154
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 4.5 and value < 6.5:
				Level3Index = 155
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 2.5 and value < 4.5:
				Level3Index = 156
			elseif chroma > 1.2 and chroma <= 2.5 and value >= 7.5:
				Level3Index = 148
			elseif chroma > 1.2 and chroma <= 2.5 and value >= 5.5:
				Level3Index = 149
			elseif chroma > 1.2 and chroma <= 2.5 and value >= 3.5:
				Level3Index = 150
			elseif (chroma > 1 and chroma <= 2.5 and value >= 2.5 and value < 3.5) || ...:
				   (chroma > 0.5 and chroma <= 2 and value >= 2 and value < 2.5)
				Level3Index = 151
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 157
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 152
			elseif chroma > 2.5 and chroma <= 7 and value >= 7.5:
				Level3Index = 143
			elseif chroma > 2.5 and chroma <= 7 and value >= 5.5:
				Level3Index = 144
			elseif chroma > 2.5 and chroma <= 7 and value >= 3.5:
				Level3Index = 145
			elseif chroma > 2 and chroma <= 7 and value >= 2:
				Level3Index = 146
			elseif chroma > 2 and chroma <= 7 and value < 2:
				Level3Index = 147
			elseif chroma > 7 and chroma <= 11 and value >= 5.5:
				Level3Index = 140
			elseif chroma > 7 and chroma <= 11 and value >= 3.5:
				Level3Index = 141
			elseif chroma > 7 and chroma <= 11 and value < 3.5:
				Level3Index = 142
			elseif chroma > 11:
				Level3Index = 139
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 43 and 49)'])
			end
			
		elseif hue >= 49 and hue < 60		%	Munsell hue range 9G-10BG, p. 26 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.2 and value >= 8.5:
				Level3Index = 153
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 6.5:
				Level3Index = 154
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 4.5 and value < 6.5:
				Level3Index = 155
			elseif chroma > 0.5 and chroma <= 1.2 and value >= 2.5 and value < 4.5:
				Level3Index = 156
			elseif chroma > 1.2 and chroma <= 2.5 and value >= 7.5:
				Level3Index = 148
			elseif chroma > 1.2 and chroma <= 2.5 and value >= 5.5:
				Level3Index = 149
			elseif chroma > 1.2 and chroma <= 2.5 and value >= 3.5:
				Level3Index = 150
			elseif (chroma > 1.2 and chroma <= 2.5 and value >= 2.5 and value < 3.5) || ...:
				   (chroma > 0.5 and chroma <= 2 and value >= 2 and value < 2.5)
				Level3Index = 151
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 157
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 152
			elseif chroma > 2.5 and chroma <= 7 and value >= 7.5:
				Level3Index = 162
			elseif chroma > 2.5 and chroma <= 7 and value >= 5.5:
				Level3Index = 163
			elseif chroma > 2.5 and chroma <= 7 and value >= 3.5:
				Level3Index = 164
			elseif chroma > 2 and chroma <= 7 and value >= 2:
				Level3Index = 165
			elseif chroma > 2 and chroma <= 7 and value < 2:
				Level3Index = 166
			elseif chroma > 7 and chroma <= 11 and value >= 5.5:
				Level3Index = 159
			elseif chroma > 7 and chroma <= 11 and value >= 3.5:
				Level3Index = 160
			elseif chroma > 7 and chroma <= 11 and value < 3.5:
				Level3Index = 161
			elseif chroma > 11:
				Level3Index = 158
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 49 and 60)'])
			end
			
		elseif hue >= 60 and hue < 69		%	Munsell hue range 10BG-9B, p. 26 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 189
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 190
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5 and value < 6.5:
				Level3Index = 191
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5 and value < 4.5:
				Level3Index = 192
			elseif chroma > 1.5 and chroma <= 3 and value >= 7.5:
				Level3Index = 184
			elseif chroma > 1.5 and chroma <= 3 and value >= 5.5:
				Level3Index = 185
			elseif chroma > 1.5 and chroma <= 3 and value >= 3:
				Level3Index = 186
			elseif (chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 3) || ...:
				   (chroma > 0.5 and chroma <= 2 and value >= 2 and value < 2.5)
				Level3Index = 187
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 193
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 188
			elseif chroma > 3 and chroma <= 7 and value >= 7.5:
				Level3Index = 171
			elseif chroma > 3 and chroma <= 7 and value >= 5.5:
				Level3Index = 172
			elseif chroma > 2 and chroma <= 7 and value >= 3.5:
				Level3Index = 173
			elseif chroma > 2 and chroma <= 7 and value >= 2:
				Level3Index = 174
			elseif chroma > 2 and chroma <= 7 and value < 2:
				Level3Index = 175
			elseif chroma > 7 and chroma <= 11 and value >= 5.5:
				Level3Index = 168
			elseif chroma > 7 and chroma <= 11 and value >= 3.5:
				Level3Index = 169
			elseif chroma > 7 and chroma <= 11 and value < 3.5:
				Level3Index = 170
			elseif chroma > 11:
				Level3Index = 167
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 60 and 69)'])
			end
			
		elseif hue >= 69 and hue < 75		%	Munsell hue range 9B-5PB, p. 27 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 189
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 190
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5 and value < 6.5:
				Level3Index = 191
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5 and value < 4.5:
				Level3Index = 192
			elseif chroma > 1.5 and chroma <= 5 and value >= 7.5:
				Level3Index = 184
			elseif chroma > 1.5 and chroma <= 5 and value >= 5.5:
				Level3Index = 185
			elseif chroma > 1.5 and chroma <= 5 and value >= 3:
				Level3Index = 186
			elseif (chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 3) || ...:
				   (chroma > 0.5 and chroma <= 2 and value >= 2 and value < 2.5)
				Level3Index = 187
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 193
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 188
			elseif chroma > 5 and chroma <= 9 and value >= 7.5:
				Level3Index = 180
			elseif chroma > 5 and chroma <= 9 and value >= 5.5:
				Level3Index = 181
			elseif chroma > 5 and chroma <= 9 and value >= 3:
				Level3Index = 182
			elseif chroma > 9 and chroma <= 13 and value >= 5.5:
				Level3Index = 177
			elseif chroma > 9 and chroma <= 13 and value > 3:
				Level3Index = 178
			elseif chroma > 2 and chroma <= 7 and value <= 3:
				Level3Index = 183
			elseif chroma > 7 and chroma <= 11 and value <= 3:
				Level3Index = 179
			elseif (chroma > 13) || ...:
				   (chroma > 11 and value <= 3)
				Level3Index = 176
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 69 and 75)'])
			end
			
		elseif hue >= 75 and hue < 76		%	Munsell hue range 5PB-6PB, p. 27 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 189
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 190
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5 and value < 6.5:
				Level3Index = 191
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5 and value < 4.5:
				Level3Index = 192
			elseif chroma > 1.5 and chroma <= 3 and value >= 7.5:
				Level3Index = 184
			elseif chroma > 1.5 and chroma <= 3 and value >= 5.5:
				Level3Index = 185
			elseif chroma > 1.5 and chroma <= 3 and value >= 3:
				Level3Index = 186
			elseif (chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 3) || ...:
				   (chroma > 0.5 and chroma <= 2 and value >= 2 and value < 2.5)
				Level3Index = 187
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 193
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 188
			elseif chroma > 3 and chroma <= 5 and value >= 7.5:
				Level3Index = 202
			elseif chroma > 3 and chroma <= 5 and value >= 4.5:
				Level3Index = 203
			elseif chroma > 3 and chroma <= 5 and value >= 3:
				Level3Index = 204
			elseif chroma > 5 and chroma <= 7 and value >= 7.5:
				Level3Index = 198
			elseif chroma > 5 and chroma <= 7 and value >= 4.5:
				Level3Index = 199
			elseif chroma > 7 and chroma <= 9 and value >= 7.5:
				Level3Index = 180
			elseif chroma > 7 and chroma <= 9 and value >= 5.5:
				Level3Index = 181
			elseif chroma > 5 and chroma <= 9 and value >= 3:
				Level3Index = 182
			elseif chroma > 9 and chroma <= 13 and value >= 5.5:
				Level3Index = 177
			elseif chroma > 9 and chroma <= 13 and value > 3:
				Level3Index = 178
			elseif chroma > 2 and chroma <= 7 and value <= 3:
				Level3Index = 183
			elseif chroma > 7 and chroma <= 11 and value <= 3:
				Level3Index = 179
			elseif (chroma > 13) || ...:
				   (chroma > 11 and value <= 3)
				Level3Index = 176
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 75 and 76)'])
			end
			
		elseif hue >= 76 and hue < 77		%	Munsell hue range 6PB-7PB, p. 28 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 189
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 190
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5 and value < 6.5:
				Level3Index = 191
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5 and value < 4.5:
				Level3Index = 192
			elseif chroma > 1.5 and chroma <= 3 and value >= 7.5:
				Level3Index = 184
			elseif chroma > 1.5 and chroma <= 3 and value >= 5.5:
				Level3Index = 185
			elseif chroma > 1.5 and chroma <= 3 and value >= 3:
				Level3Index = 186
			elseif (chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 3) || ...:
				   (chroma > 0.5 and chroma <= 2 and value >= 2 and value < 2.5)
				Level3Index = 187
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 193
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 188
			elseif chroma > 3 and chroma <= 5 and value >= 7.5:
				Level3Index = 202
			elseif chroma > 3 and chroma <= 5 and value >= 4.5:
				Level3Index = 203
			elseif chroma > 2 and chroma <= 5 and value >= 2:
				Level3Index = 204
			elseif chroma > 2 and chroma <= 5 and value <= 2:
				Level3Index = 201
			elseif chroma > 5 and chroma <= 7 and value >= 7.5:
				Level3Index = 198
			elseif chroma > 5 and chroma <= 7 and value >= 4.5:
				Level3Index = 199
			elseif chroma > 7 and chroma <= 9 and value >= 7.5:
				Level3Index = 180
			elseif chroma > 7 and chroma <= 9 and value >= 5.5:
				Level3Index = 181
			elseif chroma > 5 and chroma <= 9 and value >= 3:
				Level3Index = 182
			elseif chroma > 9 and chroma <= 13 and value >= 5.5:
				Level3Index = 177
			elseif chroma > 9 and chroma <= 13 and value > 3:
				Level3Index = 178
			elseif chroma > 2 and chroma <= 7 and value <= 3:
				Level3Index = 183
			elseif chroma > 7 and chroma <= 11 and value <= 3:
				Level3Index = 179
			elseif (chroma > 13) || ...:
				   (chroma > 11 and value <= 3)
				Level3Index = 176
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 76 and 77)'])
			end
			
		elseif hue >= 77 and hue < 79		%	Munsell hue range 7PB-9PB, p. 28 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 189
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 190
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5 and value < 6.5:
				Level3Index = 191
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5 and value < 4.5:
				Level3Index = 192
			elseif chroma > 1.5 and chroma <= 3 and value >= 7.5:
				Level3Index = 184
			elseif chroma > 1.5 and chroma <= 3 and value >= 5.5:
				Level3Index = 185
			elseif chroma > 1.5 and chroma <= 3 and value >= 3:
				Level3Index = 186
			elseif (chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 3) || ...:
				   (chroma > 0.5 and chroma <= 2 and value >= 2 and value < 2.5)
				Level3Index = 187
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 193
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 188
			elseif chroma > 3 and chroma <= 5 and value >= 7.5:
				Level3Index = 202
			elseif chroma > 3 and chroma <= 5 and value >= 4.5:
				Level3Index = 203
			elseif chroma > 2 and chroma <= 5 and value >= 2:
				Level3Index = 204
			elseif chroma > 2 and chroma <= 7 and value <= 2:
				Level3Index = 201
			elseif chroma > 5 and chroma <= 9 and value >= 7.5:
				Level3Index = 198
			elseif chroma > 5 and chroma <= 9 and value >= 4.5:
				Level3Index = 199
			elseif (chroma > 5 and chroma <= 9 and value >= 3 and value < 4.5) || ...:
				   (chroma > 5 and chroma <= 7 and value >= 2 and value < 3)
				Level3Index = 200
			elseif (chroma > 9 and chroma <= 13 and value >= 5.5) || ...:
				   (chroma > 9 and chroma <= 11 and value >= 4.5 and value < 5.5)
				Level3Index = 195
			elseif chroma > 9 and chroma <= 13 and value > 3:
				Level3Index = 196
			elseif chroma > 7 and chroma <= 11 and value <= 3:
				Level3Index = 197
			elseif (chroma > 13) || ...:
				   (chroma > 11 and value <= 3)
				Level3Index = 194
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 77 and 79)'])
			end
			
		elseif hue >= 79 and hue < 83		%	Munsell hue range 9PB-3P, p. 29 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 231
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 232
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5 and value < 6.5:
				Level3Index = 233
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5 and value < 4.5:
				Level3Index = 234
			elseif chroma > 1.5 and chroma <= 3 and value >= 7.5:
				Level3Index = 226
			elseif chroma > 1.5 and chroma <= 3 and value >= 5.5:
				Level3Index = 227
			elseif chroma > 1.5 and chroma <= 3 and value >= 3.5:
				Level3Index = 228
			elseif (chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 3.5) || ...:
				   (chroma > 0.5 and chroma <= 2 and value >= 2 and value < 2.5)
				Level3Index = 229
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 235
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 230
			elseif chroma > 3 and chroma <= 5 and value >= 7.5:
				Level3Index = 213
			elseif chroma > 3 and chroma <= 5 and value >= 4.5:
				Level3Index = 214
			elseif chroma > 3 and chroma <= 5 and value >= 2.5:
				Level3Index = 215
			elseif chroma > 2 and chroma <= 7 and value <= 2.5:
				Level3Index = 212
			elseif chroma > 5 and chroma <= 9 and value >= 7.5:
				Level3Index = 209
			elseif chroma > 5 and chroma <= 9 and value >= 4.5:
				Level3Index = 210
			elseif chroma > 5 and chroma <= 9 and value >= 2.5:
				Level3Index = 211
			elseif chroma > 9 and chroma <= 13 and value >= 4.5:
				Level3Index = 206
			elseif chroma > 9 and chroma <= 13 and value >= 2.5:
				Level3Index = 207
			elseif chroma > 7 and chroma <= 13 and value <= 2.5:
				Level3Index = 208
			elseif chroma > 13:
				Level3Index = 205
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 79 and 83)'])
			end
			
		elseif hue >= 83 and hue < 89		%	Munsell hue range 3P-9P, p. 29 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 231
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 232
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5 and value < 6.5:
				Level3Index = 233
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5 and value < 4.5:
				Level3Index = 234
			elseif (chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 3.5) || ...:
				   (chroma > 0.5 and chroma <= 2 and value >= 2 and value < 2.5)
				Level3Index = 229
			elseif chroma > 1.5 and chroma <= 5 and value >= 7.5:
				Level3Index = 226
			elseif chroma > 1.5 and chroma <= 5 and value >= 5.5:
				Level3Index = 227
			elseif chroma > 1.5 and chroma <= 5 and value >= 3.5:
				Level3Index = 228
			elseif chroma > 5 and chroma <= 9 and value >= 7.5:
				Level3Index = 221
			elseif chroma > 5 and chroma <= 9 and value >= 5.5:
				Level3Index = 222
			elseif chroma > 5 and chroma <= 9 and value >= 3.5:
				Level3Index = 223
			elseif chroma > 9 and chroma <= 13 and value >= 5.5:
				Level3Index = 217
			elseif chroma > 9 and chroma <= 13 and value >= 3.5:
				Level3Index = 218
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 235
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 230
			elseif chroma > 2 and chroma <= 7 and value >= 2 and value < 3.5:
				Level3Index = 224
			elseif chroma > 2 and chroma <= 7 and value < 2 :
				Level3Index = 225
			elseif chroma > 7 and chroma <= 13 and value >= 2 and value < 3.5:
				Level3Index = 219
			elseif chroma > 7 and chroma <= 13 and value < 2 :
				Level3Index = 220
			elseif chroma > 13:
				Level3Index = 216
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 83 and 89)'])
			end
			
		elseif hue >= 89 and hue < 93		%	Munsell hue range 9P-3RP, p. 30 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 231
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 232
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5 and value < 6.5:
				Level3Index = 233
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5 and value < 4.5:
				Level3Index = 234
			elseif (chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 3.5) || ...:
				   (chroma > 0.5 and chroma <= 2 and value >= 2 and value < 2.5)
				Level3Index = 229
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 235
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 230
			elseif chroma > 1.5 and chroma <= 5 and value >= 7.5:
				Level3Index = 252
			elseif chroma > 1.5 and chroma <= 5 and value >= 6.5:
				Level3Index = 253
			elseif chroma > 1.5 and chroma <= 3 and value >= 5.5:
				Level3Index = 227
			elseif chroma > 1.5 and chroma <= 3 and value >= 3.5:
				Level3Index = 228
			elseif chroma > 3 and chroma <= 5 and value >= 5.5 and value < 6.5:
				Level3Index = 244
			elseif chroma > 3 and chroma <= 5 and value >= 3.5 and value < 5.5:
				Level3Index = 245
			elseif chroma > 5 and chroma <= 9 and value >= 7.5:
				Level3Index = 249
			elseif chroma > 5 and chroma <= 9 and value >= 6.5:
				Level3Index = 250
			elseif chroma > 5 and chroma <= 9 and value >= 5.5:
				Level3Index = 240
			elseif chroma > 5 and chroma <= 9 and value >= 3.5:
				Level3Index = 241
			elseif chroma > 9 and value >= 7.5:
				Level3Index = 246
			elseif chroma > 9 and value >= 6.5 and value < 7.5:
				Level3Index = 247
			elseif chroma > 9 and chroma <= 15 and value >= 5.5 and value < 6.5 :
				Level3Index = 248
			elseif chroma > 9 and chroma <= 13 and value >= 3.5 and value < 5.5:
				Level3Index = 237
			elseif chroma > 13 and value < 6.5 :
				Level3Index = 236
			elseif chroma > 2 and chroma <= 7 and value >= 2 and value < 3.5:
				Level3Index = 242
			elseif chroma > 2 and chroma <= 7 and value < 2:
				Level3Index = 243
			elseif chroma > 7 and chroma <= 13 and value >= 2 and value < 3.5:
				Level3Index = 238
			elseif chroma > 7 and chroma <= 13 and value < 2:
				Level3Index = 239
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 89 and 93)'])
			end
			
		elseif hue >= 93 and hue < 99		%	Munsell hue range 3RP-9RP, p. 30 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 231
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 232
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5 and value < 6.5:
				Level3Index = 233
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5 and value < 4.5:
				Level3Index = 234
			elseif (chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 3.5) || ...:
				   (chroma > 0.5 and chroma <= 2 and value >= 2 and value < 2.5)
				Level3Index = 229
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 235
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 230
			elseif chroma > 1.5 and chroma <= 5 and value >= 7.5:
				Level3Index = 252
			elseif chroma > 1.5 and chroma <= 5 and value >= 6.5:
				Level3Index = 253
			elseif chroma > 1.5 and chroma <= 3 and value >= 5.5:
				Level3Index = 227
			elseif chroma > 1.5 and chroma <= 3 and value >= 3.5:
				Level3Index = 228
			elseif chroma > 3 and chroma <= 5 and value >= 5.5 and value < 6.5:
				Level3Index = 261
			elseif chroma > 3 and chroma <= 7 and value >= 3.5 and value < 5.5:
				Level3Index = 262
			elseif chroma > 5 and chroma <= 9 and value >= 7.5:
				Level3Index = 249
			elseif chroma > 5 and chroma <= 9 and value >= 6.5:
				Level3Index = 250
			elseif chroma > 5 and chroma <= 9 and value >= 5.5:
				Level3Index = 251
			elseif chroma > 7 and chroma <= 11 and value >= 3.5 and value < 5.5:
				Level3Index = 258
			elseif chroma > 9 and value >= 7.5:
				Level3Index = 246
			elseif chroma > 9 and value >= 6.5 and value < 7.5:
				Level3Index = 247
			elseif chroma > 9 and chroma <= 15 and value >= 5.5 and value < 6.5 :
				Level3Index = 248
			elseif chroma > 11 and chroma <= 13 and value >= 3.5 and value < 5.5:
				Level3Index = 255
			elseif chroma > 2 and chroma <= 9 and value >= 2 and value < 3.5:
				Level3Index = 259
			elseif chroma > 2 and chroma <= 7 and value < 2:
				Level3Index = 260
			elseif chroma > 9 and chroma <= 13 and value >= 2 and value < 3.5:
				Level3Index = 256
			elseif chroma > 7 and chroma <= 11 and value < 2:
				Level3Index = 257
			elseif chroma > 11 and value < 6.5 :
				Level3Index = 254
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 93 and 99)'])
			end
	
		elseif hue >= 99 || hue < 1		%	Munsell hue range 9RP-1R, p. 31 of [Kelly1976]:
			if chroma > 0.5 and chroma <= 1.5 and value >= 8.5:
				Level3Index = 9
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 6.5:
				Level3Index = 10
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 4.5:
				Level3Index = 233
			elseif chroma > 0.5 and chroma <= 1.5 and value >= 2.5:
				Level3Index = 234
			elseif (chroma > 0.5 and chroma <= 2 and value >= 2 and value < 2.5) || ...:
				   (chroma > 1.5 and chroma <= 3 and value >= 2.5 and value < 3.5)
				Level3Index = 229
			elseif chroma > 0.5 and chroma <= 1 and value < 2:
				Level3Index = 235
			elseif chroma > 1 and chroma <= 2 and value < 2:
				Level3Index = 230
			elseif chroma > 1.5 and chroma <= 3 and value >= 8:
				Level3Index = 7
			elseif chroma > 1.5 and chroma <= 3 and value >= 6.5:
				Level3Index = 8
			elseif chroma > 1.5 and chroma <= 3 and value >= 5.5:
				Level3Index = 227
			elseif chroma > 1.5 and chroma <= 3 and value >= 3.5:
				Level3Index = 228
			elseif chroma > 3 and chroma <= 7 and value >= 8:
				Level3Index = 4
			elseif chroma > 3 and chroma <= 7 and value >= 6.5 :
				Level3Index = 5
			elseif chroma > 7 and chroma <= 11 and value >= 6.5 :
				Level3Index = 2
			elseif chroma > 11 and value >= 6.5 :
				Level3Index = 1
			elseif chroma > 3 and chroma <= 5 and value >= 5.5 and value < 6.5:
				Level3Index = 261
			elseif chroma > 5 and chroma <= 7 and value >= 5.5 and value < 6.5:
				Level3Index = 6
			elseif chroma > 7 and chroma <= 15 and value >= 5.5 and value < 6.5:
				Level3Index = 3
			elseif chroma > 3 and chroma <= 7 and value >= 3.5 and value < 5.5:
				Level3Index = 262
			elseif chroma > 7 and chroma <= 11 and value >= 3.5 and value < 5.5:
				Level3Index = 258
			elseif chroma > 11 and chroma <= 13 and value >= 3.5 and value < 5.5:
				Level3Index = 255
			elseif chroma >= 2 and chroma <= 9 and value >= 2 and value < 3.5:
				Level3Index = 259
			elseif chroma > 9 and chroma <= 13 and value >= 2 and value < 3.5:
				Level3Index = 256
			elseif chroma > 2 and chroma <= 7 and value < 2 :
				Level3Index = 260
			elseif chroma > 7 and chroma <= 11 and value < 2 :
				Level3Index = 257
			elseif chroma > 11 and value < 6.5 :
				Level3Index = 254
			else
				disp(['No ISCC-NBS name found for ',MunsellSpecString,' (ASTM hue between 99 and 1)'])
			end																																																																																																																																																																																																
																																																																																																																																																																																																
		end
		
	end		# End neutral vs non-neutral

