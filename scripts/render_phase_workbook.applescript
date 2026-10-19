on fileNameFromPath(posixPath)
	set previousDelimiters to AppleScript's text item delimiters
	set AppleScript's text item delimiters to "/"
	set resultName to last text item of posixPath
	set AppleScript's text item delimiters to previousDelimiters
	return resultName
end fileNameFromPath

on run argv
	if (count of argv) < 2 then error "用法: osascript render_phase_workbook.applescript 输入.xlsx 输出.pdf [输入.xlsx 输出.pdf ...]"
	if ((count of argv) mod 2) ≠ 0 then error "用法: osascript render_phase_workbook.applescript 输入.xlsx 输出.pdf [输入.xlsx 输出.pdf ...]"

	tell application "Microsoft Excel"
		set display alerts to false
		with timeout of 600 seconds
			set totalSheetCount to 0
			repeat with argumentIndex from 1 to (count of argv) by 2
				set inputPath to item argumentIndex of argv
				set outputPath to item (argumentIndex + 1) of argv
				open workbook workbook file name inputPath read only true
				delay 2
				set openedWorkbook to active workbook
				set expectedName to my fileNameFromPath(inputPath)
				if (name of openedWorkbook) is not expectedName then error "Excel did not activate the requested workbook"
				-- Excel for Mac exports every worksheet print area when a worksheet is
				-- saved using the PDF file format.
				save as worksheet 1 of openedWorkbook filename outputPath file format PDF file format
				set totalSheetCount to totalSheetCount + (count of worksheets of openedWorkbook)
				close openedWorkbook saving no
			end repeat
		end timeout
		set display alerts to true
	end tell

	return totalSheetCount as text
end run
