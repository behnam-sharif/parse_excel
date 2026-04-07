' VBA Macro: Ungroup, Save, and Remove All Shapes with Macro, Link (including internal), Chart Type as Name, and Chart Data
Sub SaveAndRemoveAllShapes()
    Dim ws As Worksheet
    Dim shp As Shape
    Dim shpGroup As Shape
    Dim infoWs As Worksheet
    Dim row As Long
    Dim i As Integer
    Dim shapeTypeName As String
    Dim chartTypeName As String
    Dim chartData As String
    Dim cht As Chart
    Dim ser As Series
    Dim serInfo As String
    Dim axisLabel As String
    Dim linkValue As String

    ' Add a new sheet to store shape info
    Set infoWs = ThisWorkbook.Sheets.Add
    infoWs.Name = "Shape_Info"
    infoWs.Cells(1, 1).Value = "Sheet"
    infoWs.Cells(1, 2).Value = "Shape Name"
    infoWs.Cells(1, 3).Value = "Shape Type"
    infoWs.Cells(1, 4).Value = "Assigned Macro"
    infoWs.Cells(1, 5).Value = "Link"
    infoWs.Cells(1, 6).Value = "Chart Type"
    infoWs.Cells(1, 7).Value = "Chart Data"
    row = 2

    ' First, ungroup all group shapes
    For Each ws In ThisWorkbook.Worksheets
        If ws.Name <> "Shape_Info" Then
            For i = ws.Shapes.Count To 1 Step -1
                Set shpGroup = ws.Shapes(i)
                If shpGroup.Type = 6 Then ' 6 = msoGroup
                    shpGroup.Ungroup
                End If
            Next i
        End If
    Next ws

    ' Now, save info for each shape
    For Each ws In ThisWorkbook.Worksheets
        If ws.Name <> "Shape_Info" Then
            For Each shp In ws.Shapes
                infoWs.Cells(row, 1).Value = ws.Name
                infoWs.Cells(row, 2).Value = shp.Name
                ' Convert Type number to ShapeType string
                Select Case shp.Type
                    Case 1: shapeTypeName = "msoAutoShape"
                    Case 2: shapeTypeName = "msoCallout"
                    Case 3: shapeTypeName = "msoChart"
                    Case 4: shapeTypeName = "msoComment"
                    Case 5: shapeTypeName = "msoFreeform"
                    Case 6: shapeTypeName = "msoGroup"
                    Case 7: shapeTypeName = "msoEmbeddedOLEObject"
                    Case 8: shapeTypeName = "msoFormControl"
                    Case 9: shapeTypeName = "msoLine"
                    Case 10: shapeTypeName = "msoLinkedOLEObject"
                    Case 11: shapeTypeName = "msoLinkedPicture"
                    Case 12: shapeTypeName = "msoOLEControlObject"
                    Case 13: shapeTypeName = "msoPicture"
                    Case 14: shapeTypeName = "msoPlaceholder"
                    Case 15: shapeTypeName = "msoTextEffect"
                    Case 16: shapeTypeName = "msoTextBox"
                    Case 17: shapeTypeName = "msoScriptAnchor"
                    Case 18: shapeTypeName = "msoTable"
                    Case 19: shapeTypeName = "msoCanvas"
                    Case 20: shapeTypeName = "msoDiagram"
                    Case 21: shapeTypeName = "msoInk"
                    Case 22: shapeTypeName = "msoInkComment"
                    Case 24: shapeTypeName = "msoSmartArt"
                    Case 25: shapeTypeName = "msoSlicer"
                    Case 26: shapeTypeName = "msoWebVideo"
                    Case 27: shapeTypeName = "msoContentApp"
                    Case 28: shapeTypeName = "msoChart3D"
                    Case Else: shapeTypeName = "Unknown"
                End Select
                infoWs.Cells(row, 3).Value = shapeTypeName
                On Error Resume Next
                infoWs.Cells(row, 4).Value = shp.OnAction
                ' Capture both external and internal links
                linkValue = ""
                If shp.Hyperlink.Address <> "" Then
                    linkValue = shp.Hyperlink.Address
                ElseIf shp.Hyperlink.SubAddress <> "" Then
                    linkValue = shp.Hyperlink.SubAddress
                End If
                infoWs.Cells(row, 5).Value = linkValue
                On Error GoTo 0

                ' Chart info
                chartTypeName = ""
                chartData = ""
                If shp.Type = 3 Then ' msoChart
                    On Error Resume Next
                    Set cht = shp.Chart
                    If Not cht Is Nothing Then
                        chartTypeName = GetChartTypeName(cht.ChartType)
                        serInfo = ""
                        For Each ser In cht.SeriesCollection
                            axisLabel = ""
                            On Error Resume Next
                            axisLabel = ser.AxisGroup
                            On Error GoTo 0
                            serInfo = serInfo & "Series: " & ser.Name & _
                                ", DataRange: " & ser.Formula & _
                                ", AxisGroup: " & axisLabel & "; "
                        Next ser
                        chartData = serInfo
                    End If
                    On Error GoTo 0
                End If
                infoWs.Cells(row, 6).Value = chartTypeName
                infoWs.Cells(row, 7).Value = chartData

                row = row + 1
            Next shp
        End If
    Next ws

    ' Now delete all shapes
    For Each ws In ThisWorkbook.Worksheets
        If ws.Name <> "Shape_Info" Then
            For i = ws.Shapes.Count To 1 Step -1
                ws.Shapes(i).Delete
            Next i
        End If
    Next ws
End Sub

' Helper function to convert chart type number to chart type name
Function GetChartTypeName(chartType As XlChartType) As String
    Select Case chartType
        Case xlColumnClustered: GetChartTypeName = "Column Clustered"
        Case xlColumnStacked: GetChartTypeName = "Column Stacked"
        Case xlColumnStacked100: GetChartTypeName = "Column 100% Stacked"
        Case xlBarClustered: GetChartTypeName = "Bar Clustered"
        Case xlBarStacked: GetChartTypeName = "Bar Stacked"
        Case xlBarStacked100: GetChartTypeName = "Bar 100% Stacked"
        Case xlLine: GetChartTypeName = "Line"
        Case xlLineStacked: GetChartTypeName = "Line Stacked"
        Case xlLineStacked100: GetChartTypeName = "Line 100% Stacked"
        Case xlPie: GetChartTypeName = "Pie"
        Case xlPieExploded: GetChartTypeName = "Pie Exploded"
        Case xlDoughnut: GetChartTypeName = "Doughnut"
        Case xlArea: GetChartTypeName = "Area"
        Case xlAreaStacked: GetChartTypeName = "Area Stacked"
        Case xlAreaStacked100: GetChartTypeName = "Area 100% Stacked"
        Case xlXYScatter: GetChartTypeName = "XY Scatter"
        Case xlXYScatterLines: GetChartTypeName = "XY Scatter Lines"
        Case xlXYScatterLinesNoMarkers: GetChartTypeName = "XY Scatter Lines No Markers"
        Case xlXYScatterSmooth: GetChartTypeName = "XY Scatter Smooth"
        Case xlXYScatterSmoothNoMarkers: GetChartTypeName = "XY Scatter Smooth No Markers"
        Case xlBubble: GetChartTypeName = "Bubble"
        Case xlBubble3DEffect: GetChartTypeName = "Bubble 3D Effect"
        Case xlStockHLC: GetChartTypeName = "Stock HLC"
        Case xlStockOHLC: GetChartTypeName = "Stock OHLC"
        Case xlStockVHLC: GetChartTypeName = "Stock VHLC"
        Case xlStockVOHLC: GetChartTypeName = "Stock VOHLC"
        Case xlRadar: GetChartTypeName = "Radar"
        Case xlRadarFilled: GetChartTypeName = "Radar Filled"
        Case xlRadarMarkers: GetChartTypeName = "Radar Markers"
        Case xlSurface: GetChartTypeName = "Surface"
        Case xlSurfaceTopView: GetChartTypeName = "Surface Top View"
        Case xlSurfaceTopViewWireframe: GetChartTypeName = "Surface Top View Wireframe"
        Case xlSurfaceWireframe: GetChartTypeName = "Surface Wireframe"
        Case xlDoughnutExploded: GetChartTypeName = "Doughnut Exploded"
        Case xlFunnel: GetChartTypeName = "Funnel"
        Case xlTreemap: GetChartTypeName = "Treemap"
        Case xlSunburst: GetChartTypeName = "Sunburst"
        Case xlHistogram: GetChartTypeName = "Histogram"
        Case xlBoxWhisker: GetChartTypeName = "Box Whisker"
        Case xlWaterfall: GetChartTypeName = "Waterfall"
        Case xlMap: GetChartTypeName = "Map"
        Case xlCombo: GetChartTypeName = "Combo"
        Case Else: GetChartTypeName = "Other"
    End Select
End Function
