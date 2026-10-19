-- Renders a trend workbook after opening its source phase workbooks in the
-- same Excel session.  openpyxl cannot persist Excel calculation caches, so
-- opening only the trend workbook would make closed external links appear 0.
on fileNameFromPath(posixPath)
	set previousDelimiters to AppleScript's text item delimiters
	set AppleScript's text item delimiters to "/"
	set resultName to last text item of posixPath
	set AppleScript's text item delimiters to previousDelimiters
	return resultName
end fileNameFromPath

on run argv
	if (count of argv) < 3 then error "用法: 趋势.xlsx 输出.pdf 阶段1.xlsx [阶段2.xlsx ...]"
	set trendPath to item 1 of argv
	set outputPath to item 2 of argv
	tell application "Microsoft Excel"
		set display alerts to false
		with timeout of 600 seconds
			set openedSources to {}
			repeat with index from 3 to (count of argv)
				open workbook workbook file name (item index of argv) read only true
				set end of openedSources to active workbook
			end repeat
			delay 3
			open workbook workbook file name trendPath read only true
			set trendWorkbook to active workbook
			if (name of trendWorkbook) is not my fileNameFromPath(trendPath) then error "Excel did not activate the trend workbook"
			delay 3
			save as worksheet 1 of trendWorkbook filename outputPath file format PDF file format
			close trendWorkbook saving no
			repeat with sourceWorkbook in reverse of openedSources
				close sourceWorkbook saving no
			end repeat
		end timeout
		set display alerts to true
	end tell
end run
