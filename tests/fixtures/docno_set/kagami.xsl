<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns="http://www.w3.org/1999/xhtml" version="1.0">
    <xsl:output method="xml" indent="yes" omit-xml-declaration="yes"/>

<xsl:template match="DOC">
      <xsl:apply-templates select="BODY"/>
</xsl:template>

<xsl:template match="BODY">
  <html>
  <head>
    <title><xsl:value-of select="TITLE"/></title>
  </head>
  <body>
    <p align="right"><xsl:value-of select="DOCNO"/></p>
    <xsl:if test="DATE!=''">
      <p align="right"><xsl:value-of select="DATE"/></p>
    </xsl:if>
    <xsl:apply-templates select="AUTHOR"/>
    <p align="center"><xsl:value-of select="TITLE"/></p>
    <xsl:apply-templates select="MAINTXT/P"/>
    <xsl:apply-templates select="APPENDIX"/>
    <br/>
    <xsl:apply-templates select="MAINTXT2/P"/>
    <xsl:apply-templates select="APPENDIX2"/>
  </body>
  </html>
</xsl:template>

<xsl:template match="AUTHOR">
  <p align="right"><xsl:value-of select="AFF"/></p>
  <xsl:if test="NAME!=''">
    <p align="right"><xsl:value-of select="NAME"/></p>
  </xsl:if>
</xsl:template>

<xsl:template match="P">
  <p><xsl:value-of select="."/></p>
</xsl:template>

<xsl:template match="APPENDIX | APPENDIX2">
  <xsl:apply-templates select="DOCLINK"/>
  <br/>
</xsl:template>

<xsl:template match="DOCLINK">
  <a>
    <xsl:attribute name="href"><xsl:value-of select="."/></xsl:attribute>
    <xsl:attribute name="target">_blank</xsl:attribute>
    <xsl:value-of select="../APPTITLE"/>
  </a>
</xsl:template>
</xsl:stylesheet>
