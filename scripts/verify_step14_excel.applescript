on run argv
	if (count of argv) is not 7 then error "usage: verify_step14_excel.applescript PHASE1.xlsx PHASE2.xlsx PHASE3.xlsx PHASE4.xlsx PHASE5.xlsx PHASE6.xlsx TREND.xlsx"

	tell application "Microsoft Excel"
		set previousAlerts to display alerts
		set previousAskLinks to ask to update links
		set previousCalculation to calculation
		set display alerts to false
		set ask to update links to false
		set sourceBooks to {}
		set trendBook to missing value
		try
			repeat with index from 1 to 6
				open workbook workbook file name (item index of argv) read only true
				set end of sourceBooks to active workbook
			end repeat
			calculate application
			set phaseCounts to {}
			repeat with sourceIndex from 1 to (count of sourceBooks)
				set sourceBook to item sourceIndex of sourceBooks
				set calculationSheet to worksheet "_计算" of sourceBook
				set calculationRowCount to count of rows of used range of calculationSheet
				set phaseCount to missing value
				repeat with rowIndex from 2 to calculationRowCount
					if (value of range ("A" & rowIndex) of calculationSheet) is "NC总数" then
						set phaseCount to value of range ("B" & rowIndex) of calculationSheet
						exit repeat
					end if
				end repeat
				if phaseCount is missing value then error "Phase calculation sheet is missing NC总数"
				set end of phaseCounts to phaseCount
			end repeat

			open workbook workbook file name (item 7 of argv) read only true
			set trendBook to active workbook
			update link trendBook
			calculate application
			set trendPhaseOneCount to value of range "B4" of worksheet "质量工作趋势分析" of trendBook
			close trendBook saving no
			set trendBook to missing value
			repeat with sourceIndex from (count of sourceBooks) to 1 by -1
				close (item sourceIndex of sourceBooks) saving no
			end repeat
		on error errorMessage number errorNumber
			if trendBook is not missing value then close trendBook saving no
			repeat with sourceIndex from (count of sourceBooks) to 1 by -1
				close (item sourceIndex of sourceBooks) saving no
			end repeat
			set display alerts to previousAlerts
			set ask to update links to previousAskLinks
			set calculation to previousCalculation
			error errorMessage number errorNumber
		end try
		set display alerts to previousAlerts
		set ask to update links to previousAskLinks
		set calculation to previousCalculation
	end tell

	return "phase_counts=" & phaseCounts & ",trend_phase_one=" & trendPhaseOneCount
end run
