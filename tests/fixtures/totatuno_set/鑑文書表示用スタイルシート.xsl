<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" version="1.0">
<xsl:output method="html" encoding="shift_jis" indent="yes" />

<xsl:template match="DOC">
	<xsl:apply-templates select="BODY"/>
</xsl:template>

<xsl:template match="BODY">
<HTML>
	<HEAD>
		<TITLE></TITLE>
	</HEAD>
	<BODY>
		<p style="text-align:right">到達番号 ： <xsl:value-of select="TOTATUNO"/></p>
		<p style="text-align:right"><xsl:value-of select="UKEDATE"/></p>
		<br clear="all"/>
		<table align="left" cellspacing="5" style="table-layout:fixed;width:280;">
			<tr>
				<td style="text-align:left;word-break:break-all;" colspan="2"><xsl:value-of select="HOJIGRPNAME" /></td>
			</tr>
			<tr>
				<td style="text-align:left;"><xsl:value-of select="SSEISYAYKSYOK" /></td>
				<td style="text-align:left;"><xsl:value-of select="SSEISYANAME" /></td>
				<td style="text-align:left;">殿</td>
			</tr>
		</table>
		<br clear="all"/>
		<p style="text-align:right"><xsl:value-of select="ATESK"/></p>
		<p style="text-align:center"><u>電子申請に対する審査結果について</u></p>
		<br clear="all"/>
		<p><xsl:value-of select="TOTATUDATE"/>に行われた電子申請について、<xsl:value-of select="HBUN"/></p>
		<br clear="all"/>
		<table align="right" cellspacing="5">
			<tr>
				<td colspan="2"><xsl:value-of select="SSATANTOU"/></td>
			</tr>
			<tr>
				<td colspan="2"><xsl:value-of select="SYOZOKUSOSIKI"/></td>
			</tr>
			<tr>
				<td>担当者 ： </td>
				<td><xsl:value-of select="SSASYANAME"/></td>
			</tr>
			<tr>
				<td>電話番号 ： </td>
				<td><xsl:value-of select="SSASYATEL"/></td>
			</tr>
		</table>
		<br clear="all"/>
	</BODY>
</HTML>
</xsl:template>
</xsl:stylesheet>
