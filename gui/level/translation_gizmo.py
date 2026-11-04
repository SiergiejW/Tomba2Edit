"""Screen-sized native-axis translation handles, painted outside OpenGL."""
import numpy as np
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor,QPainter,QPen,QVector4D,QRegion
from PyQt6.QtWidgets import QWidget

AXES = (('X',(0.,0.,1.),'#ef6262'),('Y',(0.,-1.,0.),'#6fcf78'),('Z',(1.,0.,0.),'#64a7ff'))

class TranslationGizmo(QWidget):
    def __init__(self,viewer):
        super().__init__(viewer)
        self.viewer=viewer;self.lines=[];self.drag=None
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setMouseTracking(True)
        self.hide()

    def project(self,point,matrix):
        p=matrix*QVector4D(*(float(v)/1000 for v in point),1.)
        if p.w()<=0:return None
        return np.array(((p.x()/p.w()+1)*self.viewer.width()/2,
                         (1-p.y()/p.w())*self.viewer.height()/2))

    def sync(self):
        v=self.viewer
        instance=next((i for i in v.instances if i.index==v.selected),None)
        if (instance is None or not instance.movable or instance.authored
                or not (instance.pickup is not None or instance.placement is not None
                        or instance.native_scene_offset is not None)
                or not v.edit_mode or getattr(v,'loading',False)):
            self.hide();return
        self.setGeometry(v.rect())
        self.setToolTip('Drag X, Y or Z to move the authored position. Esc cancels. Native actions may move the entity after it spawns.')
        matrix=v._model_view_projection();anchor=np.array((instance.x,instance.y,instance.z))
        start=self.project(anchor,matrix)
        if start is None:self.hide();return
        lines=[];mask=QRegion()
        for name,axis,color in AXES:
            unit=self.project(anchor+np.array(axis)*100,matrix)
            if unit is None:continue
            delta=unit-start;length=np.linalg.norm(delta)
            if length<1:continue
            pixels_per_unit=length/100
            end=start+delta/length*72
            lines.append((name,np.array(axis),color,start,end,pixels_per_unit))
            # A narrow input region keeps navigation and selection available
            # everywhere except the visible handles.
            for step in np.linspace(.15,1,20):
                pos=start+(end-start)*step
                mask=mask.united(QRegion(round(pos[0])-7,round(pos[1])-7,14,14))
        self.lines=lines
        self.setMask(mask);self.setVisible(bool(lines));self.raise_();self.update()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for name,axis,color,start,end,scale in self.lines:
            p.setPen(QPen(QColor('#161616'),6));p.drawLine(QPointF(*start),QPointF(*end))
            p.setPen(QPen(QColor(color),3));p.drawLine(QPointF(*start),QPointF(*end))
            direction=(end-start)/np.linalg.norm(end-start);side=np.array((-direction[1],direction[0]))
            for sign in (-1,1):
                p.drawLine(QPointF(*end),QPointF(*(end-direction*11+side*sign*5)))
            p.drawText(QPointF(*(end+side*10)),name)

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.RightButton and self.drag is not None:
            self.cancel();event.accept();return
        if event.button()!=Qt.MouseButton.LeftButton:event.ignore();return
        point=np.array((event.position().x(),event.position().y()))
        for line in self.lines:
            _,axis,_,start,end,scale=line
            d=end-start;t=np.clip(np.dot(point-start,d)/np.dot(d,d),.15,1)
            if np.linalg.norm(point-(start+t*d))<=9:
                i=self.viewer.instances[self.viewer.selected]
                self.drag=(i,line,point,np.array((i.x,i.y,i.z)),
                           None if i.pickup is None else i.pickup.plane,i.angle)
                self.grabMouse();self.viewer.setFocus();event.accept();return
        event.ignore()

    def mouseMoveEvent(self,event):
        if self.drag is None:event.ignore();return
        i,line,initial,anchor,plane,angle=self.drag
        name,axis,color,start,end,scale=line
        point=np.array((event.position().x(),event.position().y()))
        direction=(end-start)/np.linalg.norm(end-start)
        target=anchor+axis*(np.dot(point-initial,direction)/scale)
        try:
            if i.pickup is not None:
                self.viewer.scene.move_pickup(i,target,axis='vertical' if name=='Y' else 'horizontal')
            else:
                if np.max(np.abs(target))>32767:raise ValueError('Coordinate outside native range')
                i.x,i.y,i.z=target;i.to_record()
            self.viewer.refresh_instance(i.index)
        except ValueError:pass
        event.accept()

    def cancel(self):
        if self.drag is None:return
        i,line,initial,anchor,plane,angle=self.drag
        i.x,i.y,i.z=anchor;i.angle=angle
        if i.pickup is not None:i.pickup.plane=plane
        if i.pickup is not None:self.viewer.scene._move_pickup_contents(i)
        i.to_record();self.viewer.refresh_instance(i.index)
        self.drag=None;self.releaseMouse()

    def mouseReleaseEvent(self,event):
        if self.drag is None:event.ignore();return
        i=self.drag[0];self.drag=None;self.releaseMouse()
        self.viewer.instance_moved.emit(i.index);event.accept()
