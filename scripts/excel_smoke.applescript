on run argv
    if (count of argv) is not 2 then error "usage: excel_smoke.applescript PHASE_XLSX TREND_XLSX"
    set phasePath to item 1 of argv
    set trendPath to item 2 of argv
    set currentStep to "initialize"

    tell application "Microsoft Excel"
        if (count of workbooks) is not 0 then error "Excel smoke requires no pre-existing workbooks; save and close them before retrying"
        set previousAlerts to display alerts
        set previousAskLinks to ask to update links
        set previousCalculation to calculation
        set previousCalculateBeforeSave to calculate before save
        set display alerts to false
        set ask to update links to false
        set calculation to calculation manual
        set calculate before save to false
        try
            set currentStep to "open initial phase workbook"
            open POSIX file phasePath
            delay 2
            if (count of workbooks) is not 1 then error "phase workbook did not open as isolated workbook 1"
            calculate application
            set initialPhaseCount to value of range "L101" of worksheet "阶段质量审核工作报告" of workbook 1
            close workbook 1 saving yes

            set currentStep to "open initial trend workbook"
            open POSIX file trendPath
            delay 2
            if (count of workbooks) is not 1 then error "trend workbook did not open as isolated workbook 1"
            update link workbook 1
            calculate application
            set initialTrendCount to value of range "B4" of worksheet "质量工作趋势分析" of workbook 1
            close workbook 1 saving yes

            set currentStep to "change checklist and recalculate phase workbook"
            open POSIX file phasePath
            delay 2
            if (count of workbooks) is not 1 then error "changed phase workbook did not open as isolated workbook 1"
            set value of range "H7" of worksheet "软件需求规格说明检查单" of workbook 1 to ""
            calculate application
            set changedAuditCount to value of range "D10" of worksheet "001产品审核报告-需求规格" of workbook 1
            set changedPhaseCount to value of range "L101" of worksheet "阶段质量审核工作报告" of workbook 1
            close workbook 1 saving yes

            set currentStep to "refresh changed trend workbook"
            open POSIX file trendPath
            delay 2
            if (count of workbooks) is not 1 then error "changed trend workbook did not open as isolated workbook 1"
            update link workbook 1
            calculate application
            set changedTrendCount to value of range "B4" of worksheet "质量工作趋势分析" of workbook 1
            close workbook 1 saving yes
        on error errorMessage number errorNumber
            if (count of workbooks) is 1 then close workbook 1 saving no
            set display alerts to previousAlerts
            set ask to update links to previousAskLinks
            set calculation to previousCalculation
            set calculate before save to previousCalculateBeforeSave
            error currentStep & ": " & errorMessage number errorNumber
        end try
        set display alerts to previousAlerts
        set ask to update links to previousAskLinks
        set calculation to previousCalculation
        set calculate before save to previousCalculateBeforeSave
    end tell

    return "initial_phase=" & initialPhaseCount & ",initial_trend=" & initialTrendCount & ",changed_audit=" & changedAuditCount & ",changed_phase=" & changedPhaseCount & ",changed_trend=" & changedTrendCount
end run
