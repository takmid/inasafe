"""
InaSAFE Disaster risk assessment tool developed by AusAid -
  **IS Utilitles implementation.**

Contact : ole.moller.nielsen@gmail.com

.. note:: This program is free software; you can redistribute it and/or modify
     it under the terms of the GNU General Public License as published by
     the Free Software Foundation; either version 2 of the License, or
     (at your option) any later version.

"""

__author__ = 'tim@linfiniti.com'
__version__ = '0.5.0'
__revision__ = '$Format:%H$'
__date__ = '29/01/2011'
__copyright__ = 'Copyright 2012, Australia Indonesia Facility for '
__copyright__ += 'Disaster Reduction'

import sys
import traceback
import logging
import math
import os
import shutil

from PyQt4 import QtCore, QtGui
from PyQt4.QtCore import QCoreApplication
from qgis.core import (QGis,
                       QgsRasterLayer,
                       QgsMapLayer,
                       QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform,
                       QgsGraduatedSymbolRendererV2,
                       QgsSymbolV2,
                       QgsRendererRangeV2,
                       QgsSymbolLayerV2Registry,
                       QgsColorRampShader,
                       QgsRasterTransparency,
                       QgsMessageLog,
                       QgsVectorLayer,
                       QgsFeature,
                       QgsVectorFileWriter)
from safe_qgis.exceptions import StyleError, ShapefileCreationError, \
    memoryLayerCreationError
#do not remove this even if it is marked as unused by your IDE
#resources are used by htmlfooter and header the comment will mark it unused
#for pylint
import safe_qgis.resources  # pylint: disable=W0611

LOGGER = logging.getLogger('InaSAFE')


def logOnQgsMessageLog(msg, tag='inaSAFE', level=0):
    QgsMessageLog.logMessage(str(msg), tag, level)


def setVectorStyle(theQgisVectorLayer, theStyle):
    """Set QGIS vector style based on InaSAFE style dictionary

    Input
        theQgisVectorLayer: Qgis layer
        theStyle: Dictionary of the form as in the example below

        {'target_field': 'DMGLEVEL',
        'style_classes':
        [{'opacity': 1, 'max': 1.5, 'colour': '#fecc5c',
          'min': 0.5, 'label': 'Low damage', 'size' : 1},
        {'opacity': 1, 'max': 2.5, 'colour': '#fd8d3c',
         'min': 1.5, 'label': 'Medium damage', 'size' : 1},
        {'opacity': 1, 'max': 3.5, 'colour': '#f31a1c',
         'min': 2.5, 'label': 'High damage', 'size' : 1}]}

        .. note:: The transparency and size keys are optional. Size applies
           to points only.
    Output
        Sets and saves style for theQgisVectorLayer

    """
    myTargetField = theStyle['target_field']
    myClasses = theStyle['style_classes']
    myGeometryType = theQgisVectorLayer.geometryType()

    myRangeList = []
    for myClass in myClasses:
        # Transparency 100: transparent
        # Transparency 0: opaque
        mySize = 2  # mm
        if 'size' in myClass:
            mySize = myClass['size']
        myTransparencyPercent = 0
        if 'transparency' in myClass:
            myTransparencyPercent = myClass['transparency']

        if 'min' not in myClass:
            raise StyleError('Style info should provide a "min" entry')
        if 'max' not in myClass:
            raise StyleError('Style info should provide a "max" entry')

        try:
            myMin = float(myClass['min'])
        except TypeError:
            raise StyleError('Class break lower bound should be a number.'
                'I got %s' % myClass['min'])

        try:
            myMax = float(myClass['max'])
        except TypeError:
            raise StyleError('Class break upper bound should be a number.'
                             'I got %s' % myClass['max'])

        myColour = myClass['colour']
        myLabel = myClass['label']
        myColour = QtGui.QColor(myColour)
        mySymbol = QgsSymbolV2.defaultSymbol(myGeometryType)
        myColourString = "%s, %s, %s" % (
                         myColour.red(),
                         myColour.green(),
                         myColour.blue())
        # Work around for the fact that QgsSimpleMarkerSymbolLayerV2
        # python bindings are missing from the QGIS api.
        # .. see:: http://hub.qgis.org/issues/4848
        # We need to create a custom symbol layer as
        # the border colour of a symbol can not be set otherwise
        myRegistry = QgsSymbolLayerV2Registry.instance()
        if myGeometryType == QGis.Point:
            myMetadata = myRegistry.symbolLayerMetadata('SimpleMarker')
            # note that you can get a list of available layer properties
            # that you can set by doing e.g.
            # QgsSimpleMarkerSymbolLayerV2.properties()
            mySymbolLayer = myMetadata.createSymbolLayer({'color_border':
                                                          myColourString})
            mySymbolLayer.setSize(mySize)
            mySymbol.changeSymbolLayer(0, mySymbolLayer)
        elif myGeometryType == QGis.Polygon:
            myMetadata = myRegistry.symbolLayerMetadata('SimpleFill')
            mySymbolLayer = myMetadata.createSymbolLayer({'color_border':
                                                          myColourString})
            mySymbol.changeSymbolLayer(0, mySymbolLayer)
        else:
            # for lines we do nothing special as the property setting
            # below should give us what we require.
            pass

        mySymbol.setColor(myColour)
        # .. todo:: Check that vectors use alpha as % otherwise scale TS
        # Convert transparency % to opacity
        # alpha = 0: transparent
        # alpha = 1: opaque
        alpha = 1 - myTransparencyPercent / 100
        mySymbol.setAlpha(alpha)
        myRange = QgsRendererRangeV2(myMin,
                                     myMax,
                                     mySymbol,
                                     myLabel)
        myRangeList.append(myRange)

    myRenderer = QgsGraduatedSymbolRendererV2('', myRangeList)
    myRenderer.setMode(QgsGraduatedSymbolRendererV2.EqualInterval)
    myRenderer.setClassAttribute(myTargetField)
    theQgisVectorLayer.setRendererV2(myRenderer)
    theQgisVectorLayer.saveDefaultStyle()


def setRasterStyle(theQgsRasterLayer, theStyle):
    """Set QGIS raster style based on InaSAFE style dictionary.

    This function will set both the colour map and the transparency
    for the passed in layer.

    Args:
        theQgsRasterLayer: Qgis layer
        style: Dictionary of the form as in the example below
        style_classes = [dict(colour='#38A800', quantity=2, transparency=0),
                         dict(colour='#38A800', quantity=5, transparency=1),
                         dict(colour='#79C900', quantity=10, transparency=1),
                         dict(colour='#CEED00', quantity=20, transparency=1),
                         dict(colour='#FFCC00', quantity=50, transparency=1),
                         dict(colour='#FF6600', quantity=100, transparency=1),
                         dict(colour='#FF0000', quantity=200, transparency=1),
                         dict(colour='#7A0000', quantity=300, transparency=1)]

    Returns:
        list: RangeList
        list: TransparencyList
    """
    # test if QGIS 1.8.0 or older
    # see issue #259
    if qgisVersion() <= 10800:
        LOGGER.debug('Rendering raster using <= 1.8 styling')
        return _setLegacyRasterStyle(theQgsRasterLayer, theStyle)
    else:
        LOGGER.debug('Rendering raster using 2+ styling')
        return _setNewRasterStyle(theQgsRasterLayer, theStyle)


def _setLegacyRasterStyle(theQgsRasterLayer, theStyle):
    """Set QGIS raster style based on InaSAFE style dictionary for QGIS < 2.0.

    This function will set both the colour map and the transparency
    for the passed in layer.

    Args:
        theQgsRasterLayer: Qgis layer
        style: Dictionary of the form as in the example below
        style_classes = [dict(colour='#38A800', quantity=2, transparency=0),
                         dict(colour='#38A800', quantity=5, transparency=1),
                         dict(colour='#79C900', quantity=10, transparency=1),
                         dict(colour='#CEED00', quantity=20, transparency=1),
                         dict(colour='#FFCC00', quantity=50, transparency=1),
                         dict(colour='#FF6600', quantity=100, transparency=1),
                         dict(colour='#FF0000', quantity=200, transparency=1),
                         dict(colour='#7A0000', quantity=300, transparency=1)]

    Returns:
        list: RangeList
        list: TransparencyList

    .. note:: There is currently a limitation in QGIS in that
       pixel transparency values can not be specified in ranges and
       consequently the opacity is of limited value and seems to
       only work effectively with integer values.

    """
    theQgsRasterLayer.setDrawingStyle(QgsRasterLayer.PalettedColor)
    myClasses = theStyle['style_classes']
    LOGGER.debug(myClasses)
    myRangeList = []
    myTransparencyList = []
    myLastValue = 0
    for myClass in myClasses:
        LOGGER.debug('Evaluating class:\n%s\n' % myClass)
        myMax = myClass['quantity']
        myColour = QtGui.QColor(myClass['colour'])
        myLabel = QtCore.QString()
        if 'label' in myClass:
            myLabel = QtCore.QString(myClass['label'])
        myShader = QgsColorRampShader.ColorRampItem(myMax, myColour, myLabel)
        myRangeList.append(myShader)

        if math.isnan(myMax):
            LOGGER.debug('Skipping class.')
            continue

        # Create opacity entries for this range
        myTransparencyPercent = 0
        if 'transparency' in myClass:
            myTransparencyPercent = int(myClass['transparency'])
        if myTransparencyPercent > 0:
            # Check if range extrema are integers so we know if we can
            # use them to calculate a value range
            if ((myLastValue == int(myLastValue)) and (myMax == int(myMax))):
                # Ensure that they are integers
                # (e.g 2.0 must become 2, see issue #126)
                myLastValue = int(myLastValue)
                myMax = int(myMax)

                # Set transparencies
                myRange = range(myLastValue, myMax)
                for myValue in myRange:
                    myPixel = \
                    QgsRasterTransparency.TransparentSingleValuePixel()
                    myPixel.pixelValue = myValue
                    myPixel.percentTransparent = myTransparencyPercent
                    myTransparencyList.append(myPixel)
                    #myLabel = myClass['label']

    # Apply the shading algorithm and design their ramp
    theQgsRasterLayer.setColorShadingAlgorithm(
        QgsRasterLayer.ColorRampShader)
    myFunction = theQgsRasterLayer.rasterShader().rasterShaderFunction()
    # Discrete will shade any cell between maxima of this break
    # and mamima of previous break to the colour of this break
    myFunction.setColorRampType(QgsColorRampShader.DISCRETE)
    myFunction.setColorRampItemList(myRangeList)

    # Now set the raster transparency
    theQgsRasterLayer.rasterTransparency()\
    .setTransparentSingleValuePixelList(myTransparencyList)

    theQgsRasterLayer.saveDefaultStyle()
    return myRangeList, myTransparencyList


def _setNewRasterStyle(theQgsRasterLayer, theStyle):
    """Set QGIS raster style based on InaSAFE style dictionary for QGIS >= 2.0.

    This function will set both the colour map and the transparency
    for the passed in layer.

    Args:
        theQgsRasterLayer: Qgis layer
        style: Dictionary of the form as in the example below
        style_classes = [dict(colour='#38A800', quantity=2, transparency=0),
                         dict(colour='#38A800', quantity=5, transparency=1),
                         dict(colour='#79C900', quantity=10, transparency=1),
                         dict(colour='#CEED00', quantity=20, transparency=1),
                         dict(colour='#FFCC00', quantity=50, transparency=1),
                         dict(colour='#FF6600', quantity=100, transparency=1),
                         dict(colour='#FF0000', quantity=200, transparency=1),
                         dict(colour='#7A0000', quantity=300, transparency=1)]

    Returns:
        list: RangeList
        list: TransparencyList
    """
    # Note imports here to prevent importing on unsupported QGIS versions
    # pylint: disable=E0611
    # pylint: disable=W0621
    # pylint: disable=W0404
    from qgis.core import (QgsRasterShader,
                           QgsColorRampShader,
                           QgsSingleBandPseudoColorRenderer,
                           QgsRasterTransparency)
    # pylint: enable=E0611
    # pylint: enable=W0621
    # pylint: enable=W0404

    myClasses = theStyle['style_classes']
    myRampItemList = []
    myTransparencyList = []
    myLastValue = 0
    for myClass in myClasses:
        LOGGER.debug('Evaluating class:\n%s\n' % myClass)
        myMax = myClass['quantity']

        if math.isnan(myMax):
            LOGGER.debug('Skipping class.')
            continue

        myColour = QtGui.QColor(myClass['colour'])
        myLabel = QtCore.QString()
        if 'label' in myClass:
            myLabel = QtCore.QString(myClass['label'])
        myRampItem = QgsColorRampShader.ColorRampItem(myMax, myColour, myLabel)
        myRampItemList.append(myRampItem)
        # Create opacity entries for this range
        myTransparencyPercent = 0
        if 'transparency' in myClass:
            myTransparencyPercent = int(myClass['transparency'])
        if myTransparencyPercent > 0:
            # Check if range extrema are integers so we know if we can
            # use them to calculate a value range
            myPixel = QgsRasterTransparency.TransparentSingleValuePixel()
            myPixel.min = myLastValue
            myPixel.max = myMax
            myPixel.percentTransparent = myTransparencyPercent
            myTransparencyList.append(myPixel)
            myLastValue = myMax

    myBand = 1  # gdal counts bands from base 1
    LOGGER.debug('Setting colour ramp list')
    myRasterShader = QgsRasterShader()
    myColorRampShader = QgsColorRampShader()
    myColorRampShader.setColorRampType(QgsColorRampShader.INTERPOLATED)
    myColorRampShader.setColorRampItemList(myRampItemList)
    LOGGER.debug('Setting shader function')
    myRasterShader.setRasterShaderFunction(myColorRampShader)
    LOGGER.debug('Setting up renderer')
    myRenderer = QgsSingleBandPseudoColorRenderer(
        theQgsRasterLayer.dataProvider(),
        myBand,
        myRasterShader)
    LOGGER.debug('Assigning renderer to raster layer')
    theQgsRasterLayer.setRenderer(myRenderer)
    LOGGER.debug('Setting raster transparency list')
    #if len(myTransparencyList) > 0:
    #    myRasterTransparency = QgsRasterTransparency()
    #    myRasterTransparency.setTransparentSingleValuePixelList(
    #        myTransparencyList)
    #    myRenderer.setRasterTransparency(myRasterTransparency)
    LOGGER.debug('Saving style as default')
    theQgsRasterLayer.saveDefaultStyle()
    LOGGER.debug('Setting raster style done!')
    return myRampItemList, myTransparencyList


def tr(theText):
    """We define a tr() alias here since the utilities implementation below
    is not a class and does not inherit from QObject.
    .. note:: see http://tinyurl.com/pyqt-differences
    Args:
       theText - string to be translated
    Returns:
       Translated version of the given string if available, otherwise
       the original string.
    """
    myContext = "Utilities"
    return QCoreApplication.translate(myContext, theText)


def getExceptionWithStacktrace(e, html=False, context=None):
    """Convert exception into a string and and stack trace

    Input
        e: Exception object
        html: Optional flat if output is to wrapped as html
        context: Optional context message

    Output
        Exception with stack trace info suitable for display
    """

    myTraceback = ''.join(traceback.format_tb(sys.exc_info()[2]))

    if not html:
        if str(e) is None or str(e) == '':
            myErrorMessage = (e.__class__.__name__ + ' : ' +
                              tr('No details provided'))
        else:
            myErrorMessage = e.__class__.__name__ + ' : ' + str(e)
        return myErrorMessage + "\n" + myTraceback
    else:
        if str(e) is None or str(e) == '':
            myErrorMessage = ('<b>' + e.__class__.__name__ + '</b> : ' +
                              tr('No details provided'))
        else:
            myErrorMessage = '<b>' + e.__class__.__name__ + '</b> : ' + str(e)

        myTraceback = ('<pre id="traceback" class="prettyprint"'
              ' style="display: none;">\n' + myTraceback + '</pre>')

        # Wrap string in html
        s = '<table class="condensed">'
        if context is not None and context != '':
            s += ('<tr><th class="warning button-cell">'
                  + tr('Error:') + '</th></tr>\n'
                  '<tr><td>' + context + '</td></tr>\n')
        # now the string from the error itself
        s += ('<tr><th class="problem button-cell">'
              + tr('Problem:') + '</th></tr>\n'
            '<tr><td>' + myErrorMessage + '</td></tr>\n')
            # now the traceback heading
        s += ('<tr><th class="info button-cell" style="cursor:pointer;"'
              ' onclick="$(\'#traceback\').toggle();">'
              + tr('Click for Diagnostic Information:') + '</th></tr>\n'
              '<tr><td>' + myTraceback + '</td></tr>\n')
        s += '</table>'
        return s


def getWGS84resolution(theLayer):
    """Return resolution of raster layer in EPSG:4326

    Input
        theLayer: Raster layer
    Output
        resolution.

    If input layer is already in EPSG:4326, simply return the resolution
    If not, work it out based on EPSG:4326 representations of its extent
    """

    msg = tr('Input layer to getWGS84resolution must be a raster layer. '
           'I got: %s' % str(theLayer.type())[1:-1])
    if not theLayer.type() == QgsMapLayer.RasterLayer:
        raise RuntimeError(msg)

    if theLayer.crs().authid() == 'EPSG:4326':
        # If it is already in EPSG:4326, simply use the native resolution
        myCellSize = theLayer.rasterUnitsPerPixel()
    else:
        # Otherwise, work it out based on EPSG:4326 representations of
        # its extent

        # Reproject extent to EPSG:4326
        myGeoCrs = QgsCoordinateReferenceSystem()
        myGeoCrs.createFromId(4326, QgsCoordinateReferenceSystem.EpsgCrsId)
        myXForm = QgsCoordinateTransform(theLayer.crs(), myGeoCrs)
        myExtent = theLayer.extent()
        myProjectedExtent = myXForm.transformBoundingBox(myExtent)

        # Estimate cellsize
        myColumns = theLayer.width()
        myGeoWidth = abs(myProjectedExtent.xMaximum() -
                         myProjectedExtent.xMinimum())
        myCellSize = myGeoWidth / myColumns

    return myCellSize


def htmlHeader():
    """Get a standard html header for wrapping content in."""
    myFile = QtCore.QFile(':/plugins/inasafe/header.html')
    if not myFile.open(QtCore.QIODevice.ReadOnly):
        return '----'
    myStream = QtCore.QTextStream(myFile)
    myHeader = myStream.readAll()
    myFile.close()
    return myHeader


def htmlFooter():
    """Get a standard html footer for wrapping content in."""
    myFile = QtCore.QFile(':/plugins/inasafe/footer.html')
    if not myFile.open(QtCore.QIODevice.ReadOnly):
        return '----'
    myStream = QtCore.QTextStream(myFile)
    myFooter = myStream.readAll()
    myFile.close()
    return myFooter


def qgisVersion():
    """Get the version of QGIS
   Args:
       None
    Returns:
        QGIS Version where 10700 represents QGIS 1.7 etc.
    Raises:
       None
    """
    myVersion = None
    try:
        myVersion = unicode(QGis.QGIS_VERSION_INT)
    except AttributeError:
        myVersion = unicode(QGis.qgisVersion)[0]
    myVersion = int(myVersion)
    return myVersion


def copyInMemory(vLayer, copyName=''):
    """Return a memory copy of a layer

    Input
        origLayer: layer
        copyName: the name of the copy
    Output
        memory copy of a layer

    """

    if copyName is '':
        copyName = vLayer.name() + ' TMP'

    if vLayer.type() == QgsMapLayer.VectorLayer:
        vType = vLayer.geometryType()
        if vType == QGis.Point:
            typeStr = 'Point'
        elif vType == QGis.Line:
            typeStr = 'Line'
        elif vType == QGis.Polygon:
            typeStr = 'Polygon'
        else:
            raise memoryLayerCreationError('Layer is whether Point or Line or '
                                           'Polygon')
    else:
        raise memoryLayerCreationError('Layer is not a VectorLayer')

    crs = vLayer.crs().authid().toLower()
    uri = typeStr + '?crs=' + crs + '&index=yes'
    memLayer = QgsVectorLayer(uri, copyName, 'memory')
    memProvider = memLayer.dataProvider()

    vProvider = vLayer.dataProvider()
    vAttrs = vProvider.attributeIndexes()
    vFields = vProvider.fields()

    fields = []
    for i in vFields:
        fields.append(vFields[i])

    memProvider.addAttributes(fields)

    vProvider.select(vAttrs)
    ft = QgsFeature()
    while vProvider.nextFeature(ft):
        memProvider.addFeatures([ft])

    # Next two lines a workaround for a QGIS bug (lte 1.8)
    # preventing mem layer attributes being saved to shp.
    memLayer.startEditing()
    memLayer.commitChanges()

    return memLayer


def memoryLayerToShapefile(theFileName,
                           theFilePath,
                           theMemoryLayer,
                           theForceFlag=False,
                           mySourceQmlPath=''):
    """Write a memory layer to a shapefile.

    .. note:: The file will be saved into the theFilePath dir  If a qml
        matching theFileName.qml can be found it will automatically copied over
        to the output dir.
        Any existing shp by the same name will be
        overridden if theForceFlag is True, otherwise the existing file will be
        returned.

    Args:
        theFileName: str filename excluding path and ext. e.g. 'mmi-cities'
        theMemoryLayer: QGIS memory layer instance.
        theForceFlag: bool (Optional). Whether to force the overwrite
            of any existing data. Defaults to False.
        mySourceQmlPath: str (Optional). Copy the qml file
        mySourceQmlPath/theFileName.qml to theFilePath.

    Returns: str Path to the created shapefile

    Raises: ShapefileCreationError
    """
    LOGGER.debug('memoryLayerToShapefile requested.')

    LOGGER.debug(str(theMemoryLayer.dataProvider().attributeIndexes()))
    if theMemoryLayer.featureCount() < 1:
        raise ShapefileCreationError('Memory layer has no features')

    myGeoCrs = QgsCoordinateReferenceSystem()
    myGeoCrs.createFromId(4326, QgsCoordinateReferenceSystem.EpsgCrsId)

    myOutputFileBase = os.path.join(theFilePath,
        '%s.' % theFileName)
    myOutputFile = myOutputFileBase + 'shp'
    if os.path.exists(myOutputFile) and theForceFlag is not True:
        return myOutputFile
    elif os.path.exists(myOutputFile):
        try:
            os.remove(myOutputFileBase + 'shp')
            os.remove(myOutputFileBase + 'shx')
            os.remove(myOutputFileBase + 'dbf')
            os.remove(myOutputFileBase + 'prj')
        except OSError:
            LOGGER.exception('Old shape files not deleted'
                             ' - this may indicate a file permissions issue.')

    # Next two lines a workaround for a QGIS bug (lte 1.8)
    # preventing mem layer attributes being saved to shp.
    theMemoryLayer.startEditing()
    theMemoryLayer.commitChanges()

    LOGGER.debug('Writing mem layer to shp: %s' % myOutputFile)
    # Explicitly giving all options, not really needed but nice for clarity
    myErrorMessage = QtCore.QString()
    myOptions = QtCore.QStringList()
    myLayerOptions = QtCore.QStringList()
    mySelectedOnlyFlag = False
    mySkipAttributesFlag = False
    myResult = QgsVectorFileWriter.writeAsVectorFormat(
        theMemoryLayer,
        myOutputFile,
        'utf-8',
        myGeoCrs,
        "ESRI Shapefile",
        mySelectedOnlyFlag,
        myErrorMessage,
        myOptions,
        myLayerOptions,
        mySkipAttributesFlag)

    if myResult == QgsVectorFileWriter.NoError:
        LOGGER.debug('Wrote mem layer to shp: %s' % myOutputFile)
    else:
        raise ShapefileCreationError(
            'Failed with error: %s' % myResult)

    # Lastly copy over the standard qml (QGIS Style file) for the mmi.tif
    if mySourceQmlPath is not '':
        myQmlPath = os.path.join(theFilePath, '%s.qml' % theFileName)
        mySourceQml = os.path.join(mySourceQmlPath, '%s.qml' % theFileName)
        shutil.copyfile(mySourceQml, myQmlPath)

    return myOutputFile
